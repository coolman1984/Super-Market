"""All error codes the server can produce, found by reading the source (AST):
first argument of ShopError/Forbidden/Conflict/ScanError, the `code` argument of
integer()/text()/iso_date(), and the defaults of those helpers."""
import ast
import pathlib

SERVER = pathlib.Path(__file__).resolve().parents[1] / "server"
ERROR_CLASSES = {"ShopError", "Forbidden", "Conflict", "ScanError", "DomainError"}
HELPERS = {"integer": 2, "text": 1, "iso_date": 1}   # helper -> index of the positional `code` argument


def server_codes():
    codes = set()
    for path in SERVER.glob("*.py"):
        if path.name == "domain.py":      # the first prototype core, not part of the product
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", None))
                if name in ERROR_CLASSES and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    codes.add(node.args[0].value)
                if name in HELPERS:
                    idx = HELPERS[name]
                    if len(node.args) > idx and isinstance(node.args[idx], ast.Constant) and isinstance(node.args[idx].value, str):
                        codes.add(node.args[idx].value)
                    for kw in node.keywords:
                        if kw.arg == "code" and isinstance(kw.value, ast.Constant):
                            codes.add(kw.value.value)
            if isinstance(node, ast.FunctionDef) and node.name in HELPERS:
                for arg, default in zip(reversed(node.args.args), reversed(node.args.defaults)):
                    if arg.arg == "code" and isinstance(default, ast.Constant):
                        codes.add(default.value)
    return codes
