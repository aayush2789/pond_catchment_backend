# Taste

## Workflow
- Execute multi-phase plans incrementally: implement one phase at a time, report what was done in that phase, and confirm before moving on. The user may explicitly authorize batch continuation (e.g. "continue with all phases") — when they do, proceed through all remaining phases without pausing for confirmation, but still report what was done in each phase, re-run the full test suite after each, add tests for the new work, and manually verify the API live. Confidence: 0.95
- Inspect the complete repository first and provide a short reuse/modify assessment before changing anything; preserve working components, reuse existing services, and maintain backwards compatibility — never rewrite working code without a concrete reason. Confidence: 0.95
- Small bug reports get direct, low-ceremony handling: the user bundles several issues in one casual message and asks to "look into it real quick" — diagnose the root cause in the actual code, fix immediately, verify, and give a concise root-cause explanation. No phased plan or confirmation gates for small fixes. Confidence: 0.6

## Design
- Preserve the existing architecture/stack unless there is a clear technical reason; avoid over-engineering — choose the simplest design that meets the requirement. Confidence: 0.9

## Tooling
- Windows dev environment: use the `powershell` tool for running commands (`shell_command` fails there); Python is invoked from the project venv (`./venv/Scripts/python.exe`). Confidence: 0.8
