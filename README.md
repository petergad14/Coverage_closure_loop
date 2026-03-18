# Automated Coverage-Directed Stimulus Generation with AI

## Overview

This script automates the closure of functional coverage in UVM (Universal Verification Methodology) testbenches using AI-powered stimulus generation. It leverages OpenRouter's API to iteratively analyze coverage gaps and evolve test sequences until target coverage is achieved.

## Purpose

The tool addresses the challenge of **coverage closure** in hardware verification by:
- Automatically identifying uncovered functional behaviors from coverage reports
- Using AI to generate directed stimulus targeting those coverage holes
- Iteratively refining test sequences based on coverage feedback
- Reducing manual effort in achieving 100% functional coverage

## Key Features

### 1. **AI-Powered Stimulus Generation**
   - Integrates with OpenRouter API (using Kimi K2 model)
   - Analyzes coverage reports to identify UNHIT bins
   - Generates SystemVerilog UVM sequence code
   - Maintains consistency with existing UVM environment

### 2. **Git Integration**
   - Automatically commits changes with descriptive messages
   - Tracks coverage progression through commit history
   - Pushes updates to remote repository
   - Provides detailed logging for debugging

### 3. **Interactive Diff Review**
   - Shows side-by-side comparison of changes before applying them
   - Supports multiple diff tools (VS Code, WinMerge, Beyond Compare, Meld)
   - Falls back to text-based diff if no GUI tools available
   - Requires user approval before implementing changes

### 4. **Simulation Pipeline**
   - Runs ModelSim/QuestaSim simulations (`vsim`)
   - Compiles SystemVerilog files (`vlog`)
   - Extracts coverage metrics using Mentor Graphics tools (`vcover`)
   - Tracks coverage improvement across iterations

### 5. **Token & Cost Tracking**
   - Monitors API token usage (prompt and response tokens)
   - Estimates API costs per iteration
   - Provides summary statistics at program completion

## Architecture

### Main Classes

**GitManager**
```
- __init__(repo_path): Initializes or connects to a Git repository
- commit_and_push(iteration, coverage_score): Commits changes with coverage metrics and pushes to origin
```

### Core Functions

| Function | Purpose |
|----------|---------|
| `evolve_sequence()` | Calls AI API to generate improved virtual sequence code targeting coverage holes |
| `show_diff_and_confirm()` | Displays file changes and waits for user approval |
| `calculate_openrouter_cost()` | Estimates API costs based on token usage |
| `safe_subprocess_run()` | Handles Windows subprocess cleanup safely |
| `main()` | Orchestrates the coverage closure loop |

### Workflow

```
1. Backup original virtual sequence
2. Run initial baseline simulation
3. Extract baseline coverage metrics
4. For each iteration (up to MAX_ITERS):
   a. Invoke AI to analyze coverage gaps and generate improved sequence
   b. Show diff to user and request approval
   c. If approved:
      - Update virtual sequence file
      - Recompile testbench
      - Run simulation with new code
      - Extract new coverage metrics
      - Commit and push to Git
      - Check if target coverage reached
5. Generate token usage summary
```

## Configuration

### API Configuration
```python
API_KEY = ""                                    # OpenRouter API key
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL = "moonshotai/kimi-k2:free"              # AI model for stimulus generation
```

### Coverage Configuration
```python
TARGET_COVERAGE = 100                          # Goal coverage percentage
MAX_ITERS = 3                                  # Maximum iterations to attempt
```

### Project Paths
```python
PROJECT_PATH = r"e:\ZC\Graduation Project\creator"
SPEC_PDF = Path(PROJECT_PATH) / "spec/OS_Creator_Spec.pdf"
UCDB_FILE = Path(PROJECT_PATH) / "covhtmlreport/coverage.ucdb"
COVERAGE_REPORT = Path(PROJECT_PATH) / "covhtmlreport/report.txt"
COMPILE_LIST = Path(PROJECT_PATH) / "compile_files.f"
```

### UVM Context Files
- `creator_sequence_item.svh` - Transaction item definition
- `creator_sequence.svh` - Base sequence implementation
- `creator_virtual_sequence.svh` - Main test sequence (file to be evolved)
- `creator_subscriber.svh` - Coverage model
- `creator_test_base.svh` - Test base class

## Prerequisites

### Software
- Python 3.8+
- ModelSim/QuestaSim (for `vsim`, `vlog`, `vcover`)
- Git (with SSH/token authentication configured for remote push)

### Python Dependencies
```
GitPython
google-genai
PyPDF2
openai
requests
```

### API Requirements
- OpenRouter API key with access to Kimi K2 model

## Usage

1. **Configure Settings**
   - Set `API_KEY` with your OpenRouter credentials
   - Update `PROJECT_PATH` to your UVM project directory
   - Adjust `TARGET_COVERAGE` and `MAX_ITERS` as needed

2. **Run the Script**
   ```powershell
   python auto_cover_nvidia_model.py
   ```

3. **Interact with Prompts**
   - Review AI-generated changes in the diff viewer
   - Type `y` or `yes` to apply changes (or `n` to skip iteration)

4. **Monitor Progress**
   - Observe coverage improvement at each iteration
   - Check console output for token usage and costs
   - Review Git commit history for tracking

## Output

### During Execution
- Detailed debug logs with `[DEBUG]`, `[INFO]`, `[WARNING]`, `[ERROR]` prefixes
- Intermediate coverage percentages
- Token usage per API call
- Estimated costs from OpenRouter

### Final Summary
- Total tokens used across all iterations
- Coverage improvement (new - baseline)
- Final coverage percentage
- Token usage breakdown per iteration

## Error Handling

- **Git Issues**: Detected and logged; process continues without push
- **Compilation Failures**: Logged; iteration skipped
- **Simulation Failures**: Logged; iteration skipped
- **Missing Diff Tools**: Falls back to text-based diff output
- **Windows Cleanup**: Gracefully handles subprocess cleanup errors on Windows

## Safety Features

- **Backup Creation**: Original virtual sequence backed up before any changes
- **User Approval**: All AI-generated changes require user review via diff
- **Dry-Run Capability**: User can reject changes without affecting system
- **Detailed Logging**: All operations logged for debugging and auditing

## Performance Considerations

- Each iteration requires:
  - AI API call (varies with model response time)
  - Compilation (~seconds to minutes depending on testbench size)
  - Simulation run (depends on test duration and coverage collection settings)

- Token cost scales with:
  - Size of specification and context files
  - Complexity of coverage report
  - Number of iterations

## Limitations

- Requires manual approval for each AI-generated change
- Depends on AI model quality for meaningful stimulus generation
- Limited to specified maximum iterations (prevents infinite loops)
- Requires properly configured Git remote for push operations
- ModelSim/QuestaSim specific (Windows-focused implementation)

## Future Enhancements

- Automatic change approval based on coverage improvement predictions
- Support for other simulators (IVerilog, Verilator, etc.)
- Machine learning model fine-tuning on design-specific coverage patterns
- Batch processing of multiple test modules
- Integration with formal verification tools

---

**Author**: AI-Driven Verification System  
**Last Updated**: 2026  
**Status**: Production-Ready for UVM-based hardware verification projects
