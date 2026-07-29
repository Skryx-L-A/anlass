"""Small closed expression evaluator for the criteria file.

No ``eval`` on user input. A criteria file is configuration, not code: this module
parses a tiny boolean expression language (``and``/``or``/``not``, comparisons, ``in``,
list literals, a fixed set of named functions) into a syntax tree once, at
construction, and evaluates that tree against a lead's fields plus a small context
mapping. Anything the grammar does not recognise - an unknown function, an unbalanced
bracket, a stray character - is a :class:`~anlass.errors.ConfigError` at load time, not
a runtime crash on the day the criteria file gets edited.

The list-literal syntax deliberately allows unquoted, multi-word entries such as
``[bauen, implementieren, from scratch]`` (see ``profile.example/criteria.yaml``), which
is not valid Python syntax and is exactly why this is a hand-written parser instead of
``ast.parse`` over a restricted node set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from ..errors import ConfigError
from ..models import Field, Lead

__all__ = ["CheckExpression"]

_KEYWORDS = {"and", "or", "not", "in", "true", "false", "null"}

#: Well-known field names (:class:`anlass.models.Field`). An identifier in this set
#: resolves through ``lead.value`` even when the lead does not have it - it is a field
#: reference that happens to be unset (``None``), not a bare constant. Field names a
#: source or enricher invented itself are covered separately: they are simply present
#: in ``lead.fields`` and resolve the same way.
_KNOWN_FIELDS = frozenset(
    value for key, value in vars(Field).items() if not key.startswith("_") and isinstance(value, str)
)

_BRACKET_RE = re.compile(r"\[([^\[\]]*)\]")
_TOKEN_RE = re.compile(
    r"""(?:
        (?P<lparen>\()
      | (?P<rparen>\))
      | (?P<op><=|>=|==|!=|<|>)
      | (?P<comma>,)
      | (?P<string>"[^"]*"|'[^']*')
      | (?P<number>-?\d+(?:\.\d+)?)
      | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    )""",
    re.VERBOSE,
)


def _text_contains_any(args: Sequence[Any], lead: Lead) -> bool:
    if len(args) != 1 or not isinstance(args[0], list):
        raise ConfigError("text_contains_any erwartet genau eine Liste als Argument.")
    haystack = lead.text.lower()
    return any(str(term).lower() in haystack for term in args[0])


def _distance_km(args: Sequence[Any], lead: Lead) -> float:
    # Textabgleich statt echter Geodistanz: diese Stufe macht keine Netzabfrage, und ein
    # Geocoder gehoert in die Anreicherung (Stufe 3), nicht in die Bewertung. Ein Treffer
    # gegen die Referenzorte zaehlt als 0 km, sonst als "weit weg" (9999) - so bricht ein
    # Schwellwert-Vergleich ("< 60") nie mit einer Ausnahme ab, auch wenn der Ort fehlt.
    del lead
    if len(args) != 2:
        raise ConfigError("distance_km erwartet zwei Argumente: Ort und Referenzorte.")
    location, references = args
    if not location or not isinstance(references, list):
        return 9999.0
    needle = str(location).strip().lower()
    return 0.0 if any(needle == str(r).strip().lower() for r in references) else 9999.0


_FUNCTIONS: dict[str, Callable[[Sequence[Any], Lead], Any]] = {
    "text_contains_any": _text_contains_any,
    "distance_km": _distance_km,
}


@dataclass(frozen=True, slots=True)
class _Literal:
    value: Any


@dataclass(frozen=True, slots=True)
class _Name:
    identifier: str


@dataclass(frozen=True, slots=True)
class _ListLiteral:
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Call:
    func: str
    args: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class _Compare:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True, slots=True)
class _BoolOp:
    op: str  # "and" / "or"
    left: Any
    right: Any


@dataclass(frozen=True, slots=True)
class _Not:
    operand: Any


def _unquote(item: str) -> str:
    """Strip one matching pair of quotes from a list item.

    Without this a quoted entry keeps its quotes and becomes the literal search term
    ``'bauen'`` including the apostrophes, which matches nothing. The failure is silent -
    the criterion simply never fires, the score stays low, and the posting looks like a
    bad fit rather than like a broken rule. Both spellings are allowed because a criteria
    file is written by hand and YAML already forces one set of quotes onto the author.
    """
    if len(item) >= 2 and item[0] == item[-1] and item[0] in ("'", '"'):
        return item[1:-1]
    return item


def _tokenize(source: str) -> tuple[list[tuple[str, str]], dict[str, tuple[str, ...]]]:
    lists: dict[str, tuple[str, ...]] = {}
    counter = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal counter
        placeholder = f"__list_{counter}__"
        counter += 1
        items = tuple(
            _unquote(part.strip()) for part in match.group(1).split(",") if part.strip()
        )
        lists[placeholder] = items
        return placeholder

    flattened = _BRACKET_RE.sub(_replace, source)
    if "[" in flattened or "]" in flattened:
        raise ConfigError(f"Unausgeglichene eckige Klammer in '{source}'.")

    tokens: list[tuple[str, str]] = []
    pos = 0
    length = len(flattened)
    while pos < length:
        if flattened[pos].isspace():
            pos += 1
            continue
        match = _TOKEN_RE.match(flattened, pos)
        if match is None:
            raise ConfigError(f"Kann '{source}' an Position {pos} nicht lesen.")
        kind = match.lastgroup
        assert kind is not None
        value = match.group(kind)
        pos = match.end()
        if kind == "name":
            lowered = value.lower()
            if lowered in _KEYWORDS:
                kind = lowered
        tokens.append((kind, value))
    return tokens, lists


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], lists: dict[str, tuple[str, ...]], raw: str) -> None:
        self._tokens = tokens
        self._lists = lists
        self._raw = raw
        self._pos = 0

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _advance(self) -> tuple[str, str]:
        token = self._peek()
        if token is None:
            raise ConfigError(f"Ausdruck endet unerwartet: '{self._raw}'.")
        self._pos += 1
        return token

    def parse(self) -> Any:
        node = self._parse_or()
        remaining = self._peek()
        if remaining is not None:
            raise ConfigError(f"Unerwartetes Zeichen '{remaining[1]}' in Ausdruck '{self._raw}'.")
        return node

    def _parse_or(self) -> Any:
        node = self._parse_and()
        while (token := self._peek()) is not None and token[0] == "or":
            self._advance()
            node = _BoolOp("or", node, self._parse_and())
        return node

    def _parse_and(self) -> Any:
        node = self._parse_not()
        while (token := self._peek()) is not None and token[0] == "and":
            self._advance()
            node = _BoolOp("and", node, self._parse_not())
        return node

    def _parse_not(self) -> Any:
        token = self._peek()
        if token is not None and token[0] == "not":
            self._advance()
            return _Not(self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> Any:
        left = self._parse_atom()
        token = self._peek()
        if token is not None and token[0] in ("op", "in"):
            op_kind, op_value = self._advance()
            op = "in" if op_kind == "in" else op_value
            right = self._parse_atom()
            return _Compare(op, left, right)
        return left

    def _parse_atom(self) -> Any:
        kind, value = self._advance()
        if kind == "number":
            return _Literal(float(value) if "." in value else int(value))
        if kind == "string":
            return _Literal(value[1:-1])
        if kind == "true":
            return _Literal(True)
        if kind == "false":
            return _Literal(False)
        if kind == "null":
            return _Literal(None)
        if kind == "lparen":
            node = self._parse_or()
            closing = self._advance()
            if closing[0] != "rparen":
                raise ConfigError(f"Fehlende schliessende Klammer in '{self._raw}'.")
            return node
        if kind == "name":
            if value in self._lists:
                return _ListLiteral(self._lists[value])
            token = self._peek()
            if token is not None and token[0] == "lparen":
                if value not in _FUNCTIONS:
                    raise ConfigError(
                        f"Unbekannte Funktion '{value}' in '{self._raw}'. Erlaubt: "
                        f"{', '.join(sorted(_FUNCTIONS))}."
                    )
                self._advance()
                args = self._parse_args()
                return _Call(value, tuple(args))
            return _Name(value)
        raise ConfigError(f"Unerwartetes Zeichen '{value}' in Ausdruck '{self._raw}'.")

    def _parse_args(self) -> list[Any]:
        args: list[Any] = []
        token = self._peek()
        if token is not None and token[0] == "rparen":
            self._advance()
            return args
        args.append(self._parse_or())
        while (token := self._peek()) is not None and token[0] == "comma":
            self._advance()
            args.append(self._parse_or())
        closing = self._advance()
        if closing[0] != "rparen":
            raise ConfigError(f"Fehlende schliessende Klammer in '{self._raw}'.")
        return args


def _truthy(value: Any) -> bool:
    return bool(value)


def _eq(left: Any, right: Any) -> bool:
    if isinstance(left, str) and isinstance(right, str):
        return left.strip().lower() == right.strip().lower()
    return left == right


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "==":
        return _eq(left, right)
    if op == "!=":
        return not _eq(left, right)
    if op == "in":
        if isinstance(right, (list, tuple, set)):
            return any(_eq(left, item) for item in right)
        return False
    if op in ("<", "<=", ">", ">="):
        if isinstance(left, bool) or not isinstance(left, (int, float)):
            return False
        if isinstance(right, bool) or not isinstance(right, (int, float)):
            return False
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        return left >= right
    raise ConfigError(f"Unbekannter Operator '{op}'.")


def _evaluate(node: Any, resolve: Callable[[str], Any], lead: Lead) -> Any:
    if isinstance(node, _Literal):
        return node.value
    if isinstance(node, _Name):
        return resolve(node.identifier)
    if isinstance(node, _ListLiteral):
        return list(node.items)
    if isinstance(node, _Call):
        args = [_evaluate(arg, resolve, lead) for arg in node.args]
        return _FUNCTIONS[node.func](args, lead)
    if isinstance(node, _Not):
        return not _truthy(_evaluate(node.operand, resolve, lead))
    if isinstance(node, _BoolOp):
        left = _truthy(_evaluate(node.left, resolve, lead))
        if node.op == "and":
            return left and _truthy(_evaluate(node.right, resolve, lead))
        return left or _truthy(_evaluate(node.right, resolve, lead))
    if isinstance(node, _Compare):
        left = _evaluate(node.left, resolve, lead)
        right = _evaluate(node.right, resolve, lead)
        return _compare(node.op, left, right)
    raise ConfigError(f"Unbekannter Knoten im Ausdrucksbaum: {node!r}.")


class CheckExpression:
    """A parsed, validated check expression from a criteria file.

    Parsing happens once at construction, so a broken criteria file fails at startup
    with a German error naming the offending expression, not mid-run.
    """

    def __init__(self, source: str) -> None:
        self.source = source.strip()
        if not self.source:
            raise ConfigError("Eine Pruefung darf nicht leer sein.")
        tokens, lists = _tokenize(self.source)
        if not tokens:
            raise ConfigError(f"Pruefung '{source}' enthaelt keinen auswertbaren Ausdruck.")
        self._tree = _Parser(tokens, lists, self.source).parse()

    def evaluate(self, lead: Lead, context: Mapping[str, Any] | None = None) -> bool:
        """Evaluate against ``lead`` and an optional context of extra names.

        Name resolution: ``context`` wins first, then a lead field - either one the
        lead actually has, or a well-known name from :class:`anlass.models.Field`
        that happens to be unset (resolves to ``None``, not to the identifier text,
        so ``contact_name != null`` is ``False`` for a lead that never had a contact
        name, not a tautology). Anything else is a bare constant equal to its own
        text - that is how ``employment_type == vollzeit_unbefristet`` compares a
        field against a value without quoting it.
        """
        ctx = context or {}

        def resolve(identifier: str) -> Any:
            if identifier in ctx:
                return ctx[identifier]
            if identifier in lead.fields or identifier in _KNOWN_FIELDS:
                return lead.value(identifier)
            return identifier

        return bool(_evaluate(self._tree, resolve, lead))
