import os
import subprocess
import time
import json
import re
import shutil
import git  # From GitPython
import logging
import argparse
from datetime import datetime
from pathlib import Path
from PyPDF2 import PdfReader
from openai import OpenAI  # Use the OpenAI library
import sys


# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("auto_cover.log", mode="a"),
    ],
)
logger = logging.getLogger(__name__)


# Suppress subprocess cleanup errors on Windows
if sys.platform == "win32":
    import warnings
    warnings.filterwarnings("ignore")


# --- Constants ---
SUBPROCESS_TIMEOUT = 600  # 10-minute timeout for simulations
API_MAX_RETRIES = 3
API_RETRY_DELAY = 10  # seconds


class GitManager:
    def __init__(self, repo_path):
        try:
            self.repo = git.Repo(repo_path)
            logger.info(f"Connected to repo at {repo_path}")
        except git.exc.InvalidGitRepositoryError:
            logger.info("Initializing new repository...")
            self.repo = git.Repo.init(repo_path)

    def commit_and_push(self, iteration, coverage_score):
        try:
            logger.debug(f"Starting commit_and_push for iteration {iteration}")

            # Stage changes (including the updated virtual sequence)
            self.repo.git.add(A=True)
            logger.debug("Changes staged successfully")

            # Check if there are changes to commit
            untracked = self.repo.untracked_files
            staged = len(self.repo.index.diff('HEAD')) > 0
            logger.debug(f"Untracked files: {len(untracked)}, Staged changes: {staged}")

            if not staged and not untracked:
                logger.warning("No changes to commit")
                return False

            # Create a descriptive commit message
            message = f"AI Update Iteration {iteration}: Coverage reached {coverage_score}%"
            self.repo.index.commit(message)
            logger.info(f"Committed: {message}")

            # Check remotes
            remotes = self.repo.remotes
            logger.debug(f"Available remotes: {[r.name for r in remotes]}")

            if 'origin' not in [r.name for r in remotes]:
                logger.error(f"'origin' remote not found. Available remotes: {[r.name for r in remotes]}")
                return False

            # Push to origin
            origin = self.repo.remote(name='origin')
            logger.debug(f"Origin URL: {origin.url}")
            origin.push()
            logger.info("Pushed updates to remote.")
            return True

        except git.exc.GitCommandError as ge:
            logger.error(f"Git command failed: {ge}")
            logger.debug(f"Command output: {ge.stderr}")
            return False
        except Exception as e:
            logger.error(f"Failed to update git: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            return False


# ------------------------------
# CONFIGURATION
# ------------------------------
API_KEY = "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"  # REPLACE THIS with your actual key or use env var
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL = "stepfun/step-3.5-flash:free"

client = OpenAI(
    base_url=OPENROUTER_BASE_URL,
    api_key=API_KEY,
)

TARGET_COVERAGE = 99
MAX_ITERS = 3

# Token tracking
token_stats = {
    "total_prompt_tokens": 0,
    "total_response_tokens": 0,
    "total_tokens": 0,
    "iterations": {},
}


def setup_paths(project_path):
    """Initialize all project paths from the given root."""
    paths = {
        "project": Path(project_path),
        "spec_pdf": Path(project_path) / "spec/OS_Creator_Spec.pdf",
        "ucdb_file": Path(project_path) / "covhtmlreport/coverage.ucdb",
        "coverage_report": Path(project_path) / "covhtmlreport/report.txt",
        "compile_list": Path(project_path) / "compile_files.f",
        "seq_item": Path(project_path) / "creator_env/creator_sequence_item.svh",
        "base_seq": Path(project_path) / "creator_env/creator_sequence.svh",
        "virt_seq": Path(project_path) / "creator_env/creator_virtual_sequence.svh",
        "subscriber": Path(project_path) / "creator_env/creator_subscriber.svh",
        "test_base": Path(project_path) / "creator_env/creator_test_base.svh",
    }
    return paths


# ------------------------------
# CORE LOGIC
# ------------------------------

def safe_subprocess_run(cmd, stream=False, **kwargs):
    """Wrapper around subprocess.run with timeout and error handling.

    Args:
        stream: If True, output is printed live to the terminal (no capture).
                If False (default), output is captured and returned in result.
    """
    kwargs.setdefault("timeout", SUBPROCESS_TIMEOUT)
    if not stream:
        kwargs.setdefault("capture_output", True)
        kwargs.setdefault("text", True)
    try:
        result = subprocess.run(cmd, **kwargs)
        return result
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {kwargs['timeout']}s: {cmd}")
        return None
    except Exception as e:
        logger.warning(f"Subprocess error: {e}")
        return None


def read_spec_pdf(pdf_path):
    """Read and extract text from the specification PDF."""
    try:
        reader = PdfReader(str(pdf_path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        if not text.strip():
            logger.warning(f"PDF at {pdf_path} produced no extractable text.")
            return "No text could be extracted from the specification PDF."
        logger.info(f"Extracted {len(text)} characters from spec PDF ({len(reader.pages)} pages)")
        return text
    except FileNotFoundError:
        logger.error(f"Spec PDF not found: {pdf_path}")
        return "Specification PDF not found."
    except Exception as e:
        logger.error(f"Failed to read spec PDF: {e}")
        return f"Error reading specification: {e}"


def clean_ai_json(content):
    """Strip markdown code fences from AI-generated JSON if present."""
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    return content


def show_diff_and_confirm(original_file, new_code):
    """Show diff using available tools and wait for user confirmation."""
    import tempfile
    import difflib

    logger.debug("Preparing diff for user review...")

    # Create temporary file with new code
    with tempfile.NamedTemporaryFile(mode='w', suffix='.svh', delete=False) as tmp:
        tmp.write(new_code)
        tmp_path = tmp.name

    original_text = Path(original_file).read_text()

    # Try various diff tools in order of preference
    diff_tools = [
        ("VS Code", f'code --diff "{original_file}" "{tmp_path}"', True),
        ("WinMerge", ["WinMergeU", str(original_file), tmp_path], False),
        ("Beyond Compare", ["bcomp", str(original_file), tmp_path], False),
        ("Meld", ["meld", str(original_file), tmp_path], False),
    ]

    diff_opened = False
    for tool_name, cmd, use_shell in diff_tools:
        try:
            logger.info(f"Attempting to open diff with {tool_name}...")
            if use_shell:
                subprocess.run(cmd, shell=True, check=False)
            else:
                subprocess.run(cmd, check=False)
            diff_opened = True
            break
        except FileNotFoundError:
            logger.debug(f"{tool_name} not found, trying next option...")
            continue
        except Exception as e:
            logger.debug(f"{tool_name} failed: {e}")
            continue

    if not diff_opened:
        logger.warning("No GUI diff tool found. Showing text diff instead...")
        diff = list(difflib.unified_diff(
            original_text.splitlines(), new_code.splitlines(), lineterm=''
        ))
        if diff:
            print("\n[DIFF] Changes to be applied:")
            print("\n".join(diff[:50]))
            if len(diff) > 50:
                print(f"... and {len(diff) - 50} more lines")
        else:
            logger.info("No changes detected")

    # Ask for actual user confirmation
    print("\n[PROMPT] Do you want to apply these changes? (y/n): ", end="")
    user_choice = input().strip().lower()
    accepted = user_choice in ('y', 'yes')

    return tmp_path, accepted


# --- System Prompt for Coverage Closure AI ---
SYSTEM_PROMPT = """You are a Senior UVM Verification Engineer specializing in functional coverage closure
for the PCIe PHY Layer OS Creator module.

## Domain Context
You are working on the OS (Ordered Set) Creator block of a PCIe Gen1 Physical Layer.
This block receives requests from the LTSSM FSM to generate ordered sets (TS1, TS2, SKP, FTS, IDLE)
and serializes them symbol-by-symbol into a Tx OS buffer.

## Your Environment
- **UVM Testbench**: Multi-agent environment with 3 agents (global, FSM, Tx)
- **Virtual Sequencer**: `creator_virtual_sequencer` with handles:
  - `p_sequencer.sequencer_global_h` — drives reset/global signals
  - `p_sequencer.sequencer_FSM_h` — drives FSM control signals (enable, OS types, symbols)
  - `p_sequencer.sequencer_Tx_h` — drives Tx buffer signals (buffer full/empty)

## Available Sequences (you may ONLY use these, do NOT create new classes):
| Sequence Name              | Sequencer Target | Purpose |
|----------------------------|------------------|---------|
| `reset_sequence`           | `sequencer_global_h` | Assert/deassert reset |
| `LIDLE_sequence`           | `sequencer_FSM_h` | Drive L_IDLE ordered set (enable=0) |
| `TS1_sequence`             | `sequencer_FSM_h` | Full TS1 OS with PAD→Link/Lane transition |
| `TS1_intrpt_sequence`      | `sequencer_FSM_h` | TS1 with mid-stream interrupt (enable/stateChange/resetTS) |
| `TS2_sequence`             | `sequencer_FSM_h` | Full TS2 OS with PAD→Link/Lane transition |
| `TS2_intrpt_sequence`      | `sequencer_FSM_h` | TS2 with mid-stream interrupt |
| `SKP_sequence`             | `sequencer_FSM_h` | Skip ordered set (COM+3×SKP) |
| `FTS_sequence`             | `sequencer_FSM_h` | Fast Training Sequence (COM+3×FTS) |
| `IDLE_sequence`            | `sequencer_FSM_h` | Electrical Idle ordered set |
| `OS_Buffer_empty_sequence` | `sequencer_Tx_h` | Buffer not full |
| `OS_Buffer_full_sequence`  | `sequencer_Tx_h` | Buffer full (backpressure) |

## Covergroup Bins to Know About
The subscriber samples these key coverpoints:
- `i_enable_cp`: enable / no_enable
- `i_LTSSM_stateChange_cp`: state_change / no_state_change
- `i_reset_TS_count_cp`: reset_TS_count / no_reset_TS_count
- `i_OSreqNum_cp`: reqNum values 0, 1, 16, 24
- `i_OScreatorTypes_cp`: other_OS (2'b00), TS_OS (2'b01), IDLE_OS (2'b10)
- `i_OScreatorSymbol0-15_cp`: various symbol bins (COM, PAD, Link, Lane, SKP, FTS, etc.)
- `i_Tx_OSbufferFull_cp`: bufferFull / bufferNotFull
- `o_OScreator_Ack_cp`: Ack / noAck
- `o_OScreator_Data_cp`: 12 data combination bins
- `o_OScreator_valid_cp`: valid / invalid

## Critical Rules
1. ONLY modify the `body()` task of `creator_virtual_sequence`. Keep the class name and `pre_body()` EXACTLY as-is.
2. NEVER define new sequence classes — only instantiate and start the ones listed above.
3. Each sequence's `pre_body()` creates its transaction — always call `seq.start(sequencer)` (which invokes `pre_body` automatically).
4. Use `repeat(N)` around sequence starts to control how many ordered sets are generated.
5. To hit `i_OSreqNum_cp` bins, the reqNum field is randomized inside the sequences — run enough iterations for randomization to cover all bins.
6. To hit interrupt-related bins (stateChange, resetTS), use `TS1_intrpt_sequence` / `TS2_intrpt_sequence`.
7. To hit bufferFull, start `OS_Buffer_full_sequence` BEFORE FSM sequences that generate data.
8. Always start with `reset_sequence` on `sequencer_global_h`.
9. The output must be a complete, compilable `.svh` file — include all `include` guards, class header, pre_body, and body.
10. Use proper SystemVerilog syntax. Do not use pseudo-code or abbreviations.
"""


def evolve_sequence(iteration, spec_text, cov_text, paths):
    """Asks AI Model to rewrite the body of the virtual sequence."""

    current_vseq_code = paths["virt_seq"].read_text()
    item_code = paths["seq_item"].read_text()
    seq_code = paths["base_seq"].read_text()
    subscriber_code = paths["subscriber"].read_text()

    prompt = f"""
    <context>
    <specification_summary>
    {spec_text}
    </specification_summary>

    <uvm_transaction_item>
    {item_code}
    </uvm_transaction_item>

    <current_virtual_sequence>
    {current_vseq_code}
    </current_virtual_sequence>

    <base_sequence_api>
    {seq_code}
    </base_sequence_api>

    <subscriber_covergroups>
    {subscriber_code}
    </subscriber_covergroups>
    </context>

    <coverage_report_iteration_{iteration}>
    {cov_text}
    </coverage_report_iteration_{iteration}>

    <task>
    Analyze the coverage report above. For every bin marked UNHIT or with 0 hits:
    1. Identify which `creator_sequence_item` field maps to that bin.
    2. Determine which available sequence drives that field to the needed value.
    3. Add or adjust sequence starts in `body()` so the DUT exercises that exact scenario.

    Think step-by-step:
    - List each UNHIT bin and the field/value it needs.
    - Map each to a sequence + sequencer.
    - Write the updated `body()` task with enough repetitions and ordering to cover all holes.
    - If a bin requires backpressure (bufferFull=1), run `OS_Buffer_full_sequence` before the FSM sequence.
    - If a bin requires interrupts, use the `_intrpt_sequence` variants.
    </task>

    <output_format>
    Return ONLY a JSON object with this exact structure:
    {{
        "reasoning": "List each UNHIT bin you found, the field it maps to, and which sequence you added/modified to cover it.",
        "updated_code": "// Complete, compilable creator_virtual_sequence.svh file content"
    }}
    Do NOT wrap the JSON in markdown code fences. Return raw JSON only.
    </output_format>
    """

    logger.info(f"Evolving Virtual Sequence (Iteration {iteration})...")

    for attempt in range(1, API_MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            usage = response.usage

            logger.info(f"Prompt: {usage.prompt_tokens}, Completion: {usage.completion_tokens}")
            logger.info(f"Approx cost: ${calculate_openrouter_cost(usage, MODEL):.6f}")

            # Clean markdown fences and parse JSON
            content = clean_ai_json(content)
            result = json.loads(content)

            result['_token_info'] = {
                'prompt_tokens': usage.prompt_tokens,
                'response_tokens': usage.completion_tokens,
                'total_tokens': usage.total_tokens,
            }
            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI response as JSON (attempt {attempt}): {e}")
            if attempt < API_MAX_RETRIES:
                logger.info(f"Retrying in {API_RETRY_DELAY}s...")
                time.sleep(API_RETRY_DELAY)
        except Exception as e:
            logger.error(f"AI API call failed (attempt {attempt}): {e}")
            if attempt < API_MAX_RETRIES:
                logger.info(f"Retrying in {API_RETRY_DELAY}s...")
                time.sleep(API_RETRY_DELAY)

    logger.error("All API retry attempts exhausted.")
    return None


def calculate_openrouter_cost(usage, model_name):
    """Estimate cost based on OpenRouter pricing."""
    p_rate = 0.10 / 1_000_000
    c_rate = 0.40 / 1_000_000
    return (usage.prompt_tokens * p_rate) + (usage.completion_tokens * c_rate)


def main(project_path):
    paths = setup_paths(project_path)

    # 0. Backup original sequence
    backup_file = paths["virt_seq"].with_suffix(".svh.bak")
    if not backup_file.exists():
        shutil.copy(paths["virt_seq"], backup_file)
        logger.info(f"Created backup: {backup_file}")

    git_handler = GitManager(project_path)

    # Read the specification PDF
    logger.info("Reading specification PDF...")
    spec_text = read_spec_pdf(paths["spec_pdf"])

    # Initial compile step
    logger.info("Running initial compilation...")
    print("" + "=" * 60)
    print("[COMPILE OUTPUT]")
    print("=" * 60)
    compile_result = safe_subprocess_run(
        ["vlog", "-f", "compile_files.f"], cwd=project_path, stream=True
    )
    print("=" * 60)
    if compile_result is None or compile_result.returncode != 0:
        logger.error("Initial compilation failed. Please fix compile errors first.")
        return

    # Initial baseline simulation
    logger.info("Running initial baseline simulation...")
    print("\n" + "=" * 60)
    print("[SIMULATION OUTPUT - Baseline]")
    print("=" * 60)
    sim_result = safe_subprocess_run(
        ["vsim", "-c", "-do", "cov.do"], cwd=project_path, stream=True
    )
    print("=" * 60)
    if sim_result is None or sim_result.returncode != 0:
        logger.error("Initial simulation failed.")
        return

    # Get baseline coverage
    logger.info("Generating initial coverage report...")
    vcover_cmd = f'vcover report "{paths["ucdb_file"]}" -details -output "{paths["coverage_report"]}"'
    cov_result = safe_subprocess_run(vcover_cmd, cwd=project_path, shell=True)
    if cov_result is None or cov_result.returncode != 0:
        logger.error("Failed to generate baseline coverage report.")
        return

    cov_text = paths["coverage_report"].read_text()
    match = re.search(r"TOTAL COVERGROUP COVERAGE\s*:\s*([\d\.]+)", cov_text)
    baseline_cov = float(match.group(1)) if match else 0.0
    logger.info(f"Baseline Coverage: {baseline_cov}%")

    # Main iteration loop
    for i in range(1, MAX_ITERS + 1):
        logger.info(f"\n--- Iteration {i} ---")

        # 1. AI Update with diff preview
        logger.info("Generating AI improvements...")
        result = evolve_sequence(i, spec_text, cov_text, paths)

        # Track tokens from this iteration
        if result and '_token_info' in result:
            token_info = result['_token_info']
            token_stats["iterations"][i] = token_info
            token_stats["total_prompt_tokens"] += token_info['prompt_tokens']
            token_stats["total_response_tokens"] += token_info['response_tokens']
            token_stats["total_tokens"] += token_info['total_tokens']

        if result and "updated_code" in result:
            logger.info("AI changes generated successfully.")
            logger.info(f"AI reasoning: {result.get('reasoning', 'N/A')}")

            tmp_file, accepted = show_diff_and_confirm(paths["virt_seq"], result["updated_code"])

            if accepted:
                # Overwrite the actual virtual sequence file
                paths["virt_seq"].write_text(result["updated_code"])
                logger.info("Virtual sequence updated.")

                # Recompile
                logger.info("Recompiling...")
                print("\n" + "=" * 60)
                print(f"[COMPILE OUTPUT - Iteration {i}]")
                print("=" * 60)
                compile_result = safe_subprocess_run(
                    ["vlog", "-f", "compile_files.f"], cwd=project_path, stream=True
                )
                print("=" * 60)
                if compile_result is None or compile_result.returncode != 0:
                    logger.error("Compilation failed.")
                else:
                    logger.info("Compilation successful. Running simulation...")
                    print("\n" + "=" * 60)
                    print(f"[SIMULATION OUTPUT - Iteration {i}]")
                    print("=" * 60)
                    sim_result_post = safe_subprocess_run(
                        ["vsim", "-c", "-do", "cov.do"], cwd=project_path, stream=True
                    )
                    print("=" * 60)

                    if sim_result_post and sim_result_post.returncode == 0:
                        logger.info("Generating coverage report for updated code...")
                        vcover_cmd_post = f'vcover report "{paths["ucdb_file"]}" -details -output "{paths["coverage_report"]}"'
                        cov_result_post = safe_subprocess_run(
                            vcover_cmd_post, cwd=project_path, shell=True
                        )
                        if cov_result_post and cov_result_post.returncode == 0:
                            cov_text = paths["coverage_report"].read_text()  # Update for next iteration!
                            match_post = re.search(r"TOTAL COVERGROUP COVERAGE\s*:\s*([\d\.]+)", cov_text)
                            new_cov = float(match_post.group(1)) if match_post else 0.0
                            logger.info(f"Coverage after updates: {new_cov}%")
                            logger.info(f"Coverage improvement: {new_cov - baseline_cov:+.2f}%")
                            baseline_cov = new_cov  # Update baseline for next iteration

                            # Commit and push
                            push_success = git_handler.commit_and_push(i, new_cov)
                            logger.debug(f"Git push result: {push_success}")

                            if new_cov >= TARGET_COVERAGE:
                                logger.info(f"Target coverage of {TARGET_COVERAGE}% reached!")
                                break
                        else:
                            logger.error("Post-update vcover failed.")
                    else:
                        logger.error("Post-update simulation failed.")
            else:
                logger.info("User rejected changes. Skipping update for this iteration.")

            # Cleanup temporary file
            try:
                Path(tmp_file).unlink()
            except Exception:
                pass
        else:
            logger.warning("AI failed to generate updated code.")

        time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AI-driven UVM coverage closure automation."
    )
    parser.add_argument(
        "--project-path",
        default=r"e:\ZC\Graduation Project\creator",
        help="Root path of the UVM project (default: current project path)",
    )
    args = parser.parse_args()

    try:
        main(args.project_path)
    except KeyboardInterrupt:
        logger.info("Script interrupted by user.")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Print token usage summary
        print("\n" + "=" * 60)
        print("[TOKEN USAGE SUMMARY]")
        print("=" * 60)
        if token_stats["iterations"]:
            for iter_num in sorted(token_stats["iterations"].keys()):
                iter_tokens = token_stats["iterations"][iter_num]
                print(f"Iteration {iter_num}:")
                print(f"   - Prompt Tokens: {iter_tokens['prompt_tokens']}")
                print(f"   - Response Tokens: {iter_tokens['response_tokens']}")
                print(f"   - Total: {iter_tokens['total_tokens']}")

        print("\n[OVERALL STATISTICS]")
        print(f"Total Prompt Tokens: {token_stats['total_prompt_tokens']}")
        print(f"Total Response Tokens: {token_stats['total_response_tokens']}")
        print(f"Total Tokens Used: {token_stats['total_tokens']}")
        print("=" * 60)

        logger.info("Cleaning up and exiting...")
        import gc
        gc.collect()
