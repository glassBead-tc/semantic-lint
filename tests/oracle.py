"""Exhaustive reference semantics for tiny fixtures. Uses only Python and raw JSON."""

from itertools import product


def assignments(names):
    names = sorted(names)
    for values in product((False, True), repeat=len(names)):
        yield dict(zip(names, values, strict=True))


def call_key(call, binding):
    name = call.get("predicate", call.get("action"))
    args = [binding[arg[1:]] if arg.startswith("$") else arg for arg in call["args"]]
    return name + "(" + ",".join(args) + ")"


def evaluate(tree, context, binding):
    if isinstance(tree, bool):
        return tree
    if "visible" in tree:
        return context["visible:" + tree["visible"]]
    if "predicate" in tree:
        return context[call_key(tree, binding)]
    if "not" in tree:
        return not evaluate(tree["not"], context, binding)
    if "all" in tree:
        return all(evaluate(x, context, binding) for x in tree["all"])
    return any(evaluate(x, context, binding) for x in tree["any"])


def possible_contexts(ir):
    facts = {"visible:" + unit for unit in ir["profile"]["units"]}
    for name, sorts in ir["predicates"].items():
        for args in product(*(ir["domains"][s] for s in sorts)):
            facts.add(name + "(" + ",".join(args) + ")")
    for context in assignments(facts):
        if all(evaluate(g, context, {}) for g in ir["profile"].get("constraints", [])):
            yield context


def active_rules(ir, context):
    pending = []
    for rule in ir["rules"]:
        variables = rule.get("variables", {})
        for values in product(*(ir["domains"][sort] for sort in variables.values())):
            binding = dict(zip(variables, values))
            if (
                context["visible:" + rule["unit"]]
                and evaluate(rule.get("guard", True), context, binding)
                and not any(
                    evaluate(g, context, binding) for g in rule.get("exceptions", [])
                )
            ):
                pending.append((rule, binding))
    effective = []
    while pending:
        # A rule without an active superior is undefeated. Recompute after its defeats.
        def defeats(left, right):
            a, x = left
            b, y = right
            if b["id"] not in a.get("overrides", []):
                return False
            return call_key(next(iter(a["effect"].values())), x) == call_key(
                next(iter(b["effect"].values())), y
            )

        top = next(
            item
            for item in pending
            if not any(defeats(other, item) for other in pending)
        )
        effective.append(top)
        pending = [item for item in pending if item != top and not defeats(top, item)]
    return effective


def compliant(ir, rules):
    choices = []
    for name, sorts in ir["actions"].items():
        for args in product(*(ir["domains"][s] for s in sorts)):
            choices.append(name + "(" + ",".join(args) + ")")

    def satisfied(item, behavior):
        rule, binding = item
        effect = rule["effect"]
        if "must" in effect:
            return behavior[call_key(effect["must"], binding)]
        if "forbid" in effect:
            return not behavior[call_key(effect["forbid"], binding)]
        return not behavior[call_key(effect["requires"], binding)] or any(
            behavior[call_key(a, binding)] for a in effect["anyOf"]
        )

    return any(
        all(satisfied(item, behavior) for item in rules)
        for behavior in assignments(choices)
    )
