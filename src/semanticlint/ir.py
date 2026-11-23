"""Validated, solver-independent JSON contract for handwritten finite models."""

from graphlib import TopologicalSorter
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Name = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Predicate(Record):
    predicate: Name
    args: list[str]


class Visible(Record):
    visible: Name


class All(Record):
    all: list["Guard"]


class AnyOf(Record):
    any: list["Guard"]


class Not(Record):
    operand: "Guard" = Field(alias="not")


Guard = bool | Predicate | Visible | All | AnyOf | Not


class Action(Record):
    action: Name
    args: list[str]


class Must(Record):
    must: Action


class Forbid(Record):
    forbid: Action


class Requires(Record):
    requires: Action
    anyOf: list[Action] = Field(min_length=1)


class Span(Record):
    path: str = Field(min_length=1)
    line: int = Field(ge=1)
    quote: str = Field(min_length=1)


class Rule(Record):
    id: Name
    unit: Name
    source: Span
    variables: dict[Name, Name] = Field(default_factory=dict)
    guard: Guard = True
    exceptions: list[Guard] = Field(default_factory=list)
    effect: Must | Forbid | Requires
    overrides: list[Name] = Field(default_factory=list)


class Profile(Record):
    name: Name
    receiver: Name
    window: str = Field(min_length=1)
    evidence: str = Field(min_length=1)
    units: list[Name]
    constraints: list[Guard] = Field(default_factory=list)


def guard_leaves(guard):
    if isinstance(guard, (Predicate, Visible)):
        yield guard
    elif isinstance(guard, Not):
        yield from guard_leaves(guard.operand)
    elif isinstance(guard, (All, AnyOf)):
        for child in guard.all if isinstance(guard, All) else guard.any:
            yield from guard_leaves(child)


def actions(effect):
    if isinstance(effect, Must):
        return [effect.must]
    if isinstance(effect, Forbid):
        return [effect.forbid]
    return [effect.requires, *effect.anyOf]


class Package(Record):
    version: Literal["0.1"]
    profile: Profile
    domains: dict[Name, list[Name]]
    predicates: dict[Name, list[Name]]
    actions: dict[Name, list[Name]]
    rules: list[Rule]

    @model_validator(mode="after")
    def check_references(self):
        by_id = {rule.id: rule for rule in self.rules}
        if len(by_id) != len(self.rules):
            raise ValueError("duplicate rule ID")
        if len(set(self.profile.units)) != len(self.profile.units):
            raise ValueError("duplicate disclosure unit")
        if self.profile.receiver not in self.domains.get("actor", []):
            raise ValueError("receiver must belong to the actor domain")
        for sort, values in self.domains.items():
            if not values or len(values) != len(set(values)):
                raise ValueError(f"empty or duplicated domain: {sort}")
        for signature in [*self.predicates.values(), *self.actions.values()]:
            if any(sort not in self.domains for sort in signature):
                raise ValueError(f"unknown sort in signature: {signature}")

        def validate_atom(call, variables):
            if isinstance(call, Visible):
                if call.visible not in self.profile.units:
                    raise ValueError(f"unknown disclosure unit: {call.visible}")
                return
            if isinstance(call, Action):
                name, registry = call.action, self.actions
            else:
                name, registry = call.predicate, self.predicates
            if name not in registry or len(call.args) != len(registry[name]):
                raise ValueError(f"unknown symbol or wrong arity: {name}")
            for arg, sort in zip(call.args, registry[name], strict=True):
                valid = (
                    variables.get(arg[1:]) == sort
                    if arg.startswith("$")
                    else arg in self.domains[sort]
                )
                if not valid:
                    raise ValueError(f"{name}: invalid {sort} argument {arg}")

        for constraint in self.profile.constraints:
            for leaf in guard_leaves(constraint):
                validate_atom(leaf, {})
        for rule in self.rules:
            validate_atom(Visible(visible=rule.unit), {})
            if any(sort not in self.domains for sort in rule.variables.values()):
                raise ValueError(f"{rule.id}: unknown variable sort")
            for guard in [rule.guard, *rule.exceptions]:
                for leaf in guard_leaves(guard):
                    validate_atom(leaf, rule.variables)
            for call in actions(rule.effect):
                validate_atom(call, rule.variables)
            for target in rule.overrides:
                other = by_id.get(target)
                if not other or {type(rule.effect), type(other.effect)} != {
                    Must,
                    Forbid,
                }:
                    raise ValueError(f"{rule.id}: invalid override target {target}")
                left, right = actions(rule.effect)[0], actions(other.effect)[0]
                if left.action != right.action:
                    raise ValueError(f"{rule.id}: override actions differ")
        tuple(
            TopologicalSorter(
                {r.id: set(r.overrides) for r in self.rules}
            ).static_order()
        )
        return self
