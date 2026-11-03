"""Small, auditable tools shared semantically with the OpenClaw plugin."""

import ast
import math
import operator

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv,
           ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval(node, depth: int = 0):
    if depth > 20:
        raise ValueError("expression is too deep")
    if isinstance(node, ast.Expression):
        return _eval(node.body, depth + 1)
    if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left, right = _eval(node.left, depth + 1), _eval(node.right, depth + 1)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent is too large")
        return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand, depth + 1))
    raise ValueError("unsupported calculator syntax")


def calculate(expression: str) -> dict:
    expression = str(expression or "").strip()[:500]
    if not expression:
        return {"error": "empty expression"}
    try:
        value = _eval(ast.parse(expression, mode="eval"))
        if isinstance(value, complex) or not math.isfinite(float(value)):
            raise ValueError("result is not finite")
        return {"expression": expression, "result": format(value, ".15g")}
    except (SyntaxError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        return {"expression": expression, "error": f"calculation failed: {exc}"}
