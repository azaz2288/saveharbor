"""Run synthetic fault acceptance against the installed wheel, never source imports."""
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

import saveharbor


def main():
    repository = Path(__file__).resolve().parents[1]
    installed = Path(saveharbor.__file__).resolve()
    if installed.is_relative_to(repository) or "site-packages" not in installed.parts:
        raise RuntimeError("Expected installed wheel, not repository source")
    version = importlib.metadata.version("saveharbor")
    if version != "0.1.3":
        raise RuntimeError("Expected SaveHarbor 0.1.3")
    spec = importlib.util.spec_from_file_location(
        "installed_restore_faults", repository / "tests" / "test_restore_faults.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(module))
    if not result.wasSuccessful() or result.testsRun != 8:
        return 1
    command = subprocess.run([sys.executable, "-I", "-m", "saveharbor", "--help"],
                             capture_output=True, text=True)
    if command.returncode != 0 or "restore" not in command.stdout:
        raise RuntimeError("Installed CLI smoke test failed")
    print(json.dumps({"version": version, "fault_tests": result.testsRun,
                      "installed": str(installed), "cli": "passed"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
