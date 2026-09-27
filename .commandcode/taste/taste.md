# Taste

## Workflow
- Execute multi-phase plans incrementally: implement one phase at a time, report what was done in that phase, and confirm before moving on — never implement all phases in one go. After each phase, re-run the full existing test suite, add tests for the new work, and manually verify the API live. Confidence: 0.95
- Inspect the complete repository first and provide a short reuse/modify assessment before changing anything; preserve working components, reuse existing services, and maintain backwards compatibility — never rewrite working code without a concrete reason. Confidence: 0.95

## Tooling
- Windows dev environment: use the `powershell` tool for running commands (`shell_command` fails there); Python is invoked from the project venv (`./venv/Scripts/python.exe`). Confidence: 0.8
ess there is a clear technical reason; avoid over-engineering — choose the simplest design that meets the requirement. Confidence: 0.9

## Tooling
- Windows dev environment: use the `powershell` tool for running commands (`shell_command` fails there); Python is invoked from the project venv (`./venv/Scripts/python.exe`). Confidence: 0.8
