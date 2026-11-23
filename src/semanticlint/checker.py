"""Finite grounding, projected disclosure search and replayable Z3 conflicts."""

from collections import defaultdict
from dataclasses import dataclass
from graphlib import TopologicalSorter
from itertools import product

import z3

from .ir import All, AnyOf, Forbid, Must, Not, Rule, Visible, actions, guard_leaves


class Incomplete(Exception):
    pass


def solve(solver, *assumptions):
    result = solver.check(*assumptions)
    if result == z3.unknown:
        raise Incomplete(solver.reason_unknown())
    return result == z3.sat


def atom(call, bindings):
    if isinstance(call, Visible):
        return f"visible:{call.visible}"
    args = [bindings[arg[1:]] if arg.startswith("$") else arg for arg in call.args]
    name = call.action if hasattr(call, "action") else call.predicate
    return f"{name}({','.join(args)})"


def guard_expr(guard, bindings):
    if isinstance(guard, bool):
        return z3.BoolVal(guard)
    if isinstance(guard, Not):
        return z3.Not(guard_expr(guard.operand, bindings))
    if isinstance(guard, All):
        return z3.And([guard_expr(g, bindings) for g in guard.all])
    if isinstance(guard, AnyOf):
        return z3.Or([guard_expr(g, bindings) for g in guard.any])
    return z3.Bool(atom(guard, bindings))


@dataclass
class Instance:
    rule: Rule
    binding: dict[str, str]
    choices: list[str]
    facts: set[str]
    enabled: z3.BoolRef
    clause: z3.BoolRef


def ground(package):
    # Higher-priority rules precede the targets they can defeat.
    graph = TopologicalSorter()
    for rule in package.rules:
        graph.add(rule.id)
        for target in rule.overrides:
            graph.add(target, rule.id)
    priority = {name: i for i, name in enumerate(graph.static_order())}
    for rule in sorted(package.rules, key=lambda r: priority[r.id]):
        domains = [package.domains[sort] for sort in rule.variables.values()]
        for values in product(*domains):
            binding = dict(zip(rule.variables, values, strict=True))
            visible = Visible(visible=rule.unit)
            guards = [visible, rule.guard, *rule.exceptions]
            facts = {atom(leaf, binding) for g in guards for leaf in guard_leaves(g)}
            enabled = z3.And(
                guard_expr(visible, binding),
                guard_expr(rule.guard, binding),
                *(z3.Not(guard_expr(e, binding)) for e in rule.exceptions),
            )
            choices = [atom(call, binding) for call in actions(rule.effect)]
            variables = [z3.Bool("action:" + key) for key in choices]
            if isinstance(rule.effect, Must):
                clause = variables[0]
            elif isinstance(rule.effect, Forbid):
                clause = z3.Not(variables[0])
            else:
                clause = z3.Implies(variables[0], z3.Or(variables[1:]))
            yield Instance(rule, binding, choices, facts, enabled, clause)


def components(instances):
    by_action = defaultdict(list)
    for index, item in enumerate(instances):
        for action in item.choices:
            by_action[action].append(index)
    remaining = set(range(len(instances)))
    while remaining:
        stack = [min(remaining)]
        indices = []
        while stack:
            index = stack.pop()
            if index in remaining:
                remaining.remove(index)
                indices.append(index)
                for action in instances[index].choices:
                    stack.extend(by_action[action])
        yield [instances[i] for i in sorted(indices)]


def effective(group, model):
    defeated = set()
    for item in group:
        if (item.rule.id, item.choices[0]) in defeated:
            continue
        if z3.is_true(model.eval(item.enabled, model_completion=True)):
            yield item
            defeated.update((target, item.choices[0]) for target in item.rule.overrides)


def conflict_core(items, timeout):
    solver = z3.Solver()
    solver.set(timeout=timeout)
    labels = [z3.Bool(f"rule:{i}") for i in range(len(items))]
    for label, item in zip(labels, items):
        solver.add(z3.Implies(label, item.clause))
    if solve(solver, *labels):
        return []
    # Shrink in source priority order, even when Z3 can return different sufficient cores.
    core = list(range(len(labels)))
    for index in core.copy():
        smaller = [i for i in core if i != index]
        if not solve(solver, *(labels[i] for i in smaller)):
            core = smaller
    # Replay without the tracking literals or unrelated clauses.
    result = [items[i] for i in core]
    replay = z3.Solver()
    replay.set(timeout=timeout)
    clauses = [item.clause for item in result]
    if solve(replay, *clauses) or any(
        not solve(replay, *(clauses[:i] + clauses[i + 1 :]))
        for i in range(len(clauses))
    ):
        raise RuntimeError("core replay failed")
    return result


def analyze(package, limit=4096, timeout=5000):
    if limit < 1 or timeout < 1:
        raise ValueError("search limits must be positive")
    solver = z3.Solver()
    solver.set(timeout=timeout)
    constraints = package.profile.constraints
    solver.add([guard_expr(g, {}) for g in constraints])
    instances = list(ground(package))
    grounded_actions = defaultdict(set)
    for item in instances:
        grounded_actions[item.rule.id].add(item.choices[0])
    for rule in package.rules:
        for target in rule.overrides:
            if grounded_actions[rule.id].isdisjoint(grounded_actions[target]):
                raise ValueError(
                    f"{rule.id}: override cannot match a grounding of {target}"
                )
    profile_facts = {atom(leaf, {}) for g in constraints for leaf in guard_leaves(g)}
    constrained_facts = profile_facts.copy()
    profile_facts.update(f"visible:{u}" for u in package.profile.units)
    for item in instances:
        profile_facts.update(item.facts)
    report = dict(
        version="0.1",
        profile=package.profile.model_dump(by_alias=True),
        status="complete",
        ground_rules=len(instances),
        checked_contexts=0,
        limits=dict(assignments_per_component=limit, query_timeout_ms=timeout),
        findings=[],
        unfinished=[],
    )
    seen = set()
    try:
        if not solve(solver):
            raise ValueError("profile has no admissible context")
    except Incomplete as error:
        report.update(status="incomplete", unfinished=[{"reason": str(error)}])
        return report
    for group in components(instances):
        keys = sorted(set.union(*(item.facts for item in group)))
        try:
            # Enumerate only extendible projections by blocking each visited projection.
            solver.push()
            count = 0
            while solve(solver):
                if count == limit:
                    raise Incomplete("assignment limit")
                count += 1
                report["checked_contexts"] += 1
                # Choose a stable representative extension. Unconstrained facts outside
                # this component may be false without changing admissibility.
                fixed = []
                context = dict.fromkeys(sorted(profile_facts), False)
                for key in sorted(constrained_facts.union(keys)):
                    variable = z3.Bool(key)
                    context[key] = not solve(solver, *fixed, z3.Not(variable))
                    fixed.append(variable if context[key] else z3.Not(variable))
                if not solve(solver, *fixed):
                    raise RuntimeError("disclosure witness failed replay")
                model = solver.model()
                items = conflict_core(list(effective(group, model)), timeout)
                identity = tuple((x.rule.id, tuple(x.binding.items())) for x in items)
                if items and identity not in seen:
                    seen.add(identity)
                    core = [
                        dict(
                            rule=x.rule.id,
                            bindings=x.binding,
                            source=x.rule.source.model_dump(),
                            effect=x.rule.effect.model_dump(),
                            unit=x.rule.unit,
                        )
                        for x in items
                    ]
                    report["findings"].append(
                        dict(context=context, core=core, inclusion_minimal=True)
                    )
                solver.add(z3.Or([z3.Bool(k) != context[k] for k in keys]))
        except Incomplete as error:
            report["status"] = "incomplete"
            report["unfinished"].append(
                dict(rules=sorted({x.rule.id for x in group}), reason=str(error))
            )
        finally:
            solver.pop()
    return report
