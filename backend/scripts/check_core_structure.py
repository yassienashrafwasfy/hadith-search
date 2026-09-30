"""Compare original core functions: python -m scripts.check_core_structure."""

import ast
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
for filename in ("preprocess.py", "search.py", "loading.py"):
    path = f"backend/scripts/{filename}"
    original = ast.parse(
        subprocess.check_output(
            ["git", "show", f"4cd4c4a:{path}"],
            cwd=root,
            text=True,
            encoding="utf-8",
        )
    )
    current = ast.parse((root / path).read_text(encoding="utf-8"))
    functions = {node.name: node for node in current.body if isinstance(node, ast.FunctionDef)}
    for node in original.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        assert node.name in functions, f"Removed core function: {filename}:{node.name}"
        same = ast.dump(node, include_attributes=False) == ast.dump(
            functions[node.name], include_attributes=False
        )
        if filename == "preprocess.py" and node.name != "run":
            assert same, f"Preprocessing changed: {node.name}"
        if node.name in {"get_mle", "get_english_lemmatizer"}:
            assert same, f"Lemmatizer changed: {node.name}"
        print(f"{'UNCHANGED' if same else 'REVIEW DIFF'} {filename}:{node.name}")
