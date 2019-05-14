#!/usr/bin/env python

import collections
import functools
import itertools
import math

import uproot

import lark

grammar = """
start:      (NEWLINE | ";")* (statement (NEWLINE | ";")+)* statement (NEWLINE | ";")*

statement:  assignment -> pass | funcassign -> pass
funcassign: CNAME "(" [CNAME ("," CNAME)*] ")" "=" block
assignment: CNAME "=" expression

patassign:  CNAME "=" expression
          | CNAME "~" expression -> symmetric
          | CNAME "~~" expression -> allsymmetric
          | CNAME "!~" expression -> asymmetric
          | CNAME "!~~" expression -> allasymmetric
pattern:    (NEWLINE | ";")* (patassign (NEWLINE | ";")+)* patassign (NEWLINE | ";")*
block:      (NEWLINE | ";")* (statement (NEWLINE | ";")+)* expression (NEWLINE | ";")*

expression: function   -> pass
function:   or         -> pass | paramlist "=>" block
or:         and        -> pass | and "or" and
and:        not        -> pass | not "and" not
not:        comparison -> pass | "not" not
comparison: arith      -> pass | arith "==" arith -> eq | arith "!=" arith -> ne
                               | arith ">" arith -> gt  | arith ">=" arith -> ge
                               | arith "<" arith -> lt  | arith "<=" arith -> le

arith:      term          -> pass | term "+" arith  -> add | term "-" arith  -> sub
term:       factor        -> pass | factor "*" term -> mul | factor "/" term -> div
factor:     pow           -> pass | "+" factor      -> pos | "-" factor      -> neg
pow:        call          -> pass | call "**" factor
call:       atom          -> pass
          | call "(" arglist ")"  | call "[" slice "]" -> subscript | call "." CNAME -> attribute

atom:       CNAME -> symbol | INT -> int | FLOAT -> float
          | "(" expression ")" -> pass
          | "{" block "}" -> pass
          | "{" pattern "}" -> pass
          | "join" expression -> join

paramlist:  "(" [CNAME ("," CNAME)*] ")" | CNAME
arglist:    expression ("," expression)*
slice:      expression -> pass
          | expression ":" -> slice1 | ":" expression -> slice2 | expression ":" expression -> slice12

%import common.CNAME
%import common.INT
%import common.FLOAT
%import common.NEWLINE
%import common.WS
%ignore WS
"""

parser = lark.Lark(grammar)

class AST:
    _fields = ()

    def __init__(self, *args, line=None):
        self.line = line
        assert len(self._fields) == len(args)
        for n, x in zip(self._fields, args):
            setattr(self, n, x)
            if not isinstance(x, list):
                x = [x]
            for xi in x:
                if self.line is None:
                    self.line = getattr(xi, "line", None)

    def __repr__(self):
        return "{0}({1})".format(type(self).__name__, ", ".join(getattr(self, n) for n in self._fields))

    def copy(self, **replacements):
        out = type(self).__new__(type(self))
        out.__dict__ = dict(self.__dict__)
        out.__dict__.update(replacements)
        return out

class Module(AST):
    _fields = ("statements",)
    def __str__(self):
        return "\n".join(str(x) for x in self.statements)

class Assignment(AST):
    _fields = ("symbol", "operator", "expression")
    def __str__(self):
        return "{0} {1} {2}".format(self.symbol, self.operator, str(self.expression))

class Pattern(AST):
    _fields = ("assignments", "matching")
    def __init__(self, *args, **kwargs):
        super(Pattern, self).__init__(*args, **kwargs)
        self.ID = type("Line{0}".format(self.line), (ID,), {})
    def __str__(self):
        return "{" + "; ".join(str(x) for x in self.assignments) + "}"

class Join(AST):
    _fields = ("expression", "matching")
    def __str__(self):
        return "join " + str(self.expression)

class Function(AST):
    _fields = ("parameters", "body")
    def __str__(self):
        left, right = ("", "") if len(self.parameters) == 1 else ("(", ")")
        return "{0}{1}{2} => {3}".format(left, ", ".join(self.parameters), right, str(self.body))

class Block(AST):
    _fields = ("statements", "expression")
    def __str__(self):
        return "{" + "; ".join(str(x) for x in self.statements + [self.expression]) + "}"

class Call(AST):
    _fields = ("function", "arguments")
    def __str__(self):
        return "{0}({1})".format(str(self.function), ", ".join(str(x) for x in self.arguments))

class Subscript(AST):
    _fields = ("object", "index")
    def __str__(self):
        return "{0}[{1}]".format(str(self.object), str(self.index))

class Attribute(AST):
    _fields = ("object", "field")
    def __str__(self):
        return "{0}.{1}".format(str(self.object), self.field)

class Slice(AST):
    _fields = ("start", "stop")
    def __str__(self):
        return "{0}:{1}".format("" if self.start is None else str(self.start), "" if self.stop is None else str(self.stop))

class Symbol(AST):
    _fields = ("symbol",)
    def __str__(self):
        return self.symbol

class Literal(AST):
    _fields = ("value",)
    def __str__(self):
        return str(self.value)

opname = {
    "and": "and", "or": "or", "not": "not",
    "eq": "==", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<=",
    "add": "+", "sub": "-", "mul": "*", "div": "/", "pos": "+", "neg": "-", "pow": "**",
    "symmetric": "~", "allsymmetric": "~~", "asymmetric": "!~", "allasymmetric": "!~~"
    }

def toast(node, matching=None):
    if node.data == "pass":
        return toast(node.children[0], matching)
    elif node.data == "start":
        return Module([toast(x, matching) for x in node.children if not isinstance(x, lark.lexer.Token)])
    elif node.data == "funcassign":
        return Assignment(str(node.children[0]), "=", Function(node.children[1:-1], toast(node.children[-1], matching)))
    elif node.data == "assignment" or node.data == "patassign":
        return Assignment(str(node.children[0]), "=", toast(node.children[1], matching))
    elif node.data == "symmetric" or node.data == "allsymmetric" or node.data == "asymmetric" or node.data == "allasymmetric":
        if matching is None:
            raise SyntaxError("cannot use {0} operator outside of a match {...}".format(opname[node.data]))
        return Assignment(str(node.children[0]), opname[node.data], matching.replace(node.children[1]))
    elif node.data == "pattern":
        return Pattern([toast(x, matching) for x in node.children if not isinstance(x, lark.lexer.Token)], matching)
    elif node.data == "join":
        m = Matching()
        return Join(toast(node.children[0], m), m)
    elif node.data == "function":
        return Function(toast(node.children[0], matching), toast(node.children[1], None))
    elif node.data == "paramlist":
        return [str(x) for x in node.children]
    elif node.data == "block":
        items = [toast(x, matching) for x in node.children if not isinstance(x, lark.lexer.Token)]
        if len(items) == 1:
            return items[0]
        else:
            return Block(items[:-1], items[-1])
    elif node.data == "call":
        return Call(toast(node.children[0], matching), toast(node.children[1], matching))
    elif node.data == "arglist":
        return [toast(x, matching) for x in node.children]
    elif node.data == "subscript":
        return Subscript(toast(node.children[0], matching), toast(node.children[1], matching))
    elif node.data == "attribute":
        return Attribute(toast(node.children[0], matching), node.children[1])
    elif node.data == "slice1":
        return Slice(toast(node.children[0], matching), None)
    elif node.data == "slice2":
        return Slice(None, toast(node.children[0], matching))
    elif node.data == "slice12":
        return Slice(toast(node.children[0], matching), toast(node.children[1], matching))
    elif node.data == "symbol":
        return Symbol(str(node.children[0]), line=node.children[0].line)
    elif node.data == "int":
        return Literal(int(node.children[0]), line=node.children[0].line)
    elif node.data == "float":
        return Literal(float(node.children[0]), line=node.children[0].line)
    elif node.data in opname:
        return Call(Symbol(opname[node.data]), [toast(x, matching) for x in node.children])
    else:
        raise AssertionError(node.data)

class SymbolTable:
    def __init__(self, parent, symbols):
        self.parent = parent
        self.symbols = symbols

    def __getitem__(self, symbol):
        if symbol in self.symbols:
            return self.symbols[symbol]
        elif self.parent is not None:
            return self.parent[symbol]
        else:
            raise KeyError(symbol)

    def __setitem__(self, symbol, value):
        if symbol in self.symbols:
            raise TypeError("symbol {0} has multiple definitions at this scope".format(repr(symbol)))
        else:
            self.symbols[symbol] = value

    def __str__(self):
        return "{" + ",\n ".join(repr(n) + ": " + repr(x) for n, x in self.symbols.items() if not callable(x)) + "}"

@functools.total_ordering
class ID:
    def __init__(self, n):
        self.n = n
    def __repr__(self):
        return "{0}({1})".format(type(self).__name__, self.n)
    def __hash__(self):
        return hash((type(self), self.n))
    def __eq__(self, other):
        return type(self) == type(other) and self.n == other.n
    def __ne__(self, other):
        return not self == other
    def __lt__(self, other):
        if type(self) is type(other):
            return self.n < other.n
        else:
            return type(self).__name__ < type(other).__name__

class obj:
    def __init__(self, id, **fields):
        self._id = id
        self._fields = collections.OrderedDict(fields.items())
    def __contains__(self, n):
        return n in self._fields
    def __getitem__(self, n):
        return self._fields[n]
    def __setitem__(self, n, x):
        self._fields[n] = x
    def __getattr__(self, n):
        if n in self._fields:
            return self._fields[n]
        else:
            raise AttributeError("{0} object has no attribute {1}".format(repr(type(self).__name__), repr(n)))
    def __repr__(self):
        return "{0}({1}{2})".format(type(self).__name__, self._id, "".join(", {0}={1}".format(n, repr(x)) for n, x in self._fields.items()))

class Matching:
    class Placeholder:
        def __init__(self, m, n):
            self.m = m
            self.n = n
        def __repr__(self):
            return "Placeholder({0}, {1})".format(self.m, self.n)
        def value(self):
            return self.m.row[self.n]

    def __init__(self):
        self.collections = []

    def __repr__(self):
        return "<Matching at 0x{0:012x}>".format(id(self))

    def replace(self, node):
        self.collections.append(toast(node, self))
        return self.Placeholder(self, len(self.collections) - 1)

    def run(self, node, symbols):
        evaluated = [run(x, symbols) for x in self.collections]
        for i, row in enumerate(itertools.product(*evaluated)):
            self.i, self.row = i, row
            yield run(node, symbols)

def run(node, symbols):
    if isinstance(node, Module):
        for x in node.statements:
            run(x, symbols)

    elif isinstance(node, Assignment):
        if isinstance(node.expression, Matching.Placeholder):
            symbols[node.symbol] = node.expression.value()
        else:
            symbols[node.symbol] = run(node.expression, symbols)

    elif isinstance(node, Join):
        return list(node.matching.run(node.expression, symbols))
        
    elif isinstance(node, Pattern):
        if node.matching is None:
            out = obj(node.ID(None))
        else:
            out = obj(node.ID(node.matching.i))
        symbols = SymbolTable(symbols, out)
        for x in node.assignments:
            run(x, symbols)
        return out

    elif isinstance(node, Function):
        def function(arguments, symbols):
            if len(node.parameters) != len(arguments):
                raise TypeError("on line {0}, function expects {1} arguments, got {2}".format(node.line, len(node.parameters), len(arguments)))
            scope = {}
            for param, arg in zip(node.parameters, arguments):
                scope[param] = run(arg, symbols)
            return run(node.body, SymbolTable(symbols, scope))
        return function

    elif isinstance(node, Block):
        symbols = SymbolTable(symbols, {})
        for x in node.statements:
            run(x, symbols)
        return run(node.expression, symbols)

    elif isinstance(node, Call):
        try:
            return run(node.function, symbols)(node.arguments, symbols)
        except Exception as err:
            raise RuntimeError("on line {0}, encountered {1}: {2}".format("???" if node.line is None else node.line, type(err).__name__, str(err)))

    elif isinstance(node, Subscript):
        try:
            return run(node.object, symbols)[run(node.index, symbols)]
        except Exception as err:
            raise RuntimeError("on line {0}, encountered {1}: {2}".format("???" if node.line is None else node.line, type(err).__name__, str(err)))

    elif isinstance(node, Attribute):
        try:
            return getattr(run(node.object, symbols), node.field)
        except Exception as err:
            raise RuntimeError("on line {0}, encountered {1}: {2}".format("???" if node.line is None else node.line, type(err).__name__, str(err)))
        
    elif isinstance(node, Slice):
        return slice(None if node.start is None else run(node.start, symbols), None if node.stop is None else run(node.stop, symbols), None)

    elif isinstance(node, Symbol):
        try:
            return symbols[node.symbol]
        except Exception as err:
            raise RuntimeError("on line {0}, encountered {1}: {2}".format("???" if node.line is None else node.line, type(err).__name__, str(err)))
        
    elif isinstance(node, Literal):
        return node.value

    else:
        raise AssertionError(type(node))

builtins = SymbolTable(None, {
    "len":  lambda arguments, symbols: len(run(arguments[0], symbols)),
    "abs":  lambda arguments, symbols: abs(run(arguments[0], symbols)),

    "and":  lambda arguments, symbols: run(arguments[0], symbols) and run(arguments[1], symbols),
    "or":   lambda arguments, symbols: run(arguments[0], symbols) or run(arguments[1], symbols),
    "not":  lambda arguments, symbols: not run(arguments[0], symbols),

    "==":   lambda arguments, symbols: run(arguments[0], symbols) == run(arguments[1], symbols),
    "!=":   lambda arguments, symbols: run(arguments[0], symbols) != run(arguments[1], symbols),
    ">":    lambda arguments, symbols: run(arguments[0], symbols) > run(arguments[1], symbols),
    ">=":   lambda arguments, symbols: run(arguments[0], symbols) >= run(arguments[1], symbols),
    "<":    lambda arguments, symbols: run(arguments[0], symbols) < run(arguments[1], symbols),
    "<=":   lambda arguments, symbols: run(arguments[0], symbols) <= run(arguments[1], symbols),
    "+":    lambda arguments, symbols: +run(arguments[0], symbols) if len(arguments) == 1 else (run(arguments[0], symbols) + run(arguments[1], symbols)),
    "-":    lambda arguments, symbols: -run(arguments[0], symbols) if len(arguments) == 1 else (run(arguments[0], symbols) - run(arguments[1], symbols)),
    "*":    lambda arguments, symbols: run(arguments[0], symbols) * run(arguments[1], symbols),
    "/":    lambda arguments, symbols: float(run(arguments[0], symbols)) / float(run(arguments[1], symbols)),
    "**":   lambda arguments, symbols: run(arguments[0], symbols) ** run(arguments[1], symbols),

    "sqrt": lambda arguments, symbols: math.sqrt(run(arguments[0], symbols)),
    "exp":  lambda arguments, symbols: math.exp(run(arguments[0], symbols)),
    "sin":  lambda arguments, symbols: math.sin(run(arguments[0], symbols)),
    "cos":  lambda arguments, symbols: math.cos(run(arguments[0], symbols)),
    "tan":  lambda arguments, symbols: math.tan(run(arguments[0], symbols)),
    "sinh": lambda arguments, symbols: math.sinh(run(arguments[0], symbols)),
    "cosh": lambda arguments, symbols: math.cosh(run(arguments[0], symbols)),
    "tanh": lambda arguments, symbols: math.tanh(run(arguments[0], symbols)),

    "union": lambda arguments, symbols: sum((run(x, symbols) for x in arguments), []),   # all sorts of unwarranted assumptions
    })

class LorentzVector:
    def __init__(self, px, py, pz, E):
        self.px = px
        self.py = py
        self.pz = pz
        self.E = E
    def __repr__(self):
        return "LorentzVector({0:g}, {1:g}, {2:g}, {3:g})".format(self.px, self.py, self.pz, self.E)
    @property
    def pt(self):
        return math.sqrt(self.px**2 + self.py**2)
    @property
    def mass(self):
        try:
            return math.sqrt(self.E**2 - self.px**2 - self.py**2 - self.pz**2)
        except ValueError:
            return float("nan")
    def __add__(self, other):
        return LorentzVector(self.px + other.px, self.py + other.py, self.pz + other.pz, self.E + other.E)

symbols = SymbolTable(builtins, {"x": LorentzVector(1, 2, 3, 4)})
run(toast(parser.parse("""
quad(x, y) = sqrt(x**2 + y**2)
q = quad(x.pz, x.E)
"""), None), symbols)
print(symbols)

Q = collections.namedtuple("Q", ["id", "q"])
class X(ID): pass
class Y(ID): pass

# symbols = SymbolTable(builtins, {"x": [Q(X(0), 1.1), Q(X(1), 2.2), Q(X(2), 3.3)], "y": [Q(Y(0), "A"), Q(Y(1), "B")]})
symbols = SymbolTable(builtins, {"x": [1.1, 2.2, 3.3], "y": ["A", "B"]})
run(toast(parser.parse("""
z = join {
    xi ~ x
    yi ~ y
}
""")), symbols)
print(symbols)

# print(toast(parser.parse("""
# higgs =
#     join {
#         z1 ~ {
#             mu1 ~ muons
#             mu2 ~ muons
#             p4 = mu1.p4 + mu2.p4
#         }
#         z2 ~ {
#             mu1 ~ muons
#             mu2 ~ muons
#             p4 = mu1.p4 + mu2.p4
#         }
#         p4 = z1.p4 + z2.p4
#     }
# """)))

# print(toast(parser.parse("""
# genreco = join {
#     gen ~ generator
#     reco = join reconstructed
# }
# """)))

# symbols = SymbolTable(builtins, {"generator": [1, 2, 3], "reconstructed": [1.1, 3.3]})
# run(toast(parser.parse("""
# diff(x, y) = abs(x - y)
# genreco = match {
#     if diff(gen, reco) < 0.5
#     for gen in generator, reco in reconstructed
# }
# """)), symbols)
# print(symbols)

# events = uproot.open("http://scikit-hep.org/uproot/examples/HZZ.root")["events"]
# Electron_Px, Electron_Py, Electron_Pz, Electron_E, Electron_Charge = events.arrays(["Electron_Px", "Electron_Py", "Electron_Pz", "Electron_E", "Electron_Charge"], outputtype=tuple, entrystop=5)
# Muon_Px, Muon_Py, Muon_Pz, Muon_E, Muon_Charge = events.arrays(["Muon_Px", "Muon_Py", "Muon_Pz", "Muon_E", "Muon_Charge"], outputtype=tuple, entrystop=5)
# Particle = collections.namedtuple("Particle", ["px", "py", "pz", "E", "charge"])

# engine = toast(parser.parse("""
# mass(particle) = sqrt(particle.E**2 - particle.px**2 - particle.py**2 - particle.pz**2)

# same_flavor(collection) = match {
#     z1 = lep1 + lep2
#     z2 = lep3 + lep4
#     hmass = mass(z1 + z2)
#     if lep1.charge != lep2.charge
#     if lep3.charge != lep4.charge
#     sort (mass(z1) - 91)**2 + (mass(z2) - 91)**2
#     for lep1, lep2, lep3, lep4 in collection
# }

# higgs4e = same_flavor(electrons)
# higgs4mu = same_flavor(muons)

# higgs2e2mu = match {
#     z1 = lep1 + lep2
#     z2 = lep3 + lep4
#     hmass = mass(z1 + z2)
#     if lep1.charge != lep2.charge
#     if lep3.charge != lep4.charge
#     for lep1, lep2 in electrons, lep3, lep4 in muons
# }
# """))

# for i in range(len(Muon_Px)):
#     symbols = SymbolTable(builtins, {
#         "electrons": [Particle(Electron_Px[i][j], Electron_Py[i][j], Electron_Pz[i][j], Electron_E[i][j], Electron_Charge[i][j]) for j in range(len(Electron_Px[i]))],
#         "muons": [Particle(Muon_Px[i][j], Muon_Py[i][j], Muon_Pz[i][j], Muon_E[i][j], Muon_Charge[i][j]) for j in range(len(Muon_Px[i]))]
#         })
#     run(engine, symbols)
#     del symbols["electrons"]
#     del symbols["muons"]
#     print(symbols)
