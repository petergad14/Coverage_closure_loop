import os
import subprocess
import time
import json
import re
import shutil
import git  # From GitPython
from datetime import datetime
from pathlib import Path
from google import genai
from google.genai import types
from PyPDF2 import PdfReader
import sys
import requests
import json
from openai import OpenAI # Use the OpenAI library


# Suppress subprocess cleanup errors on Windows
if sys.platform == "win32":
    import warnings
    warnings.filterwarnings("ignore")


# Helper function for safer subprocess cleanup on Windows
def safe_subprocess_run(cmd, **kwargs):
    """Wrapper around subprocess.run that handles Windows cleanup issues gracefully."""
    try:
        return subprocess.run(cmd, **kwargs)
    except Exception as e:
        print(f"[WARNING] Subprocess error: {e}")
        return None


class GitManager:
    def __init__(self, repo_path):
        try:
            self.repo = git.Repo(repo_path)
            print(f"[GIT] Connected to repo at {repo_path}")
        except git.exc.InvalidGitRepositoryError:
            print("[GIT] Initializing new repository...")
            self.repo = git.Repo.init(repo_path)

    def commit_and_push(self, iteration, coverage_score):
        try:
            print(f"[GIT DEBUG] Starting commit_and_push for iteration {iteration}")
            
            # Stage changes (including the updated virtual sequence)
            print("[GIT DEBUG] Staging all changes...")
            self.repo.git.add(A=True)
            print("[GIT DEBUG] Changes staged successfully")
            
            # Check if there are changes to commit
            untracked = self.repo.untracked_files
            staged = len(self.repo.index.diff('HEAD')) > 0
            print(f"[GIT DEBUG] Untracked files: {len(untracked)}, Staged changes: {staged}")
            
            if not staged and not untracked:
                print("[GIT WARNING] No changes to commit")
                return False
            
            # Create a descriptive commit message
            message = f"AI Update Iteration {iteration}: Coverage reached {coverage_score}%"
            print(f"[GIT DEBUG] Committing with message: {message}")
            
            # Commit locally
            self.repo.index.commit(message)
            print(f"[GIT] Committed: {message}")
            
            # Check remotes
            remotes = self.repo.remotes
            print(f"[GIT DEBUG] Available remotes: {[r.name for r in remotes]}")
            
            if 'origin' not in [r.name for r in remotes]:
                print("[GIT ERROR] 'origin' remote not found. Available remotes: {}".format([r.name for r in remotes]))
                return False
            
            # Push to origin (ensure you have set up a remote and SSH/Token)
            print("[GIT DEBUG] Attempting to push to 'origin'...")
            origin = self.repo.remote(name='origin')
            print(f"[GIT DEBUG] Origin URL: {origin.url}")
            origin.push()
            print("[GIT] Pushed updates to remote.")
            return True
            
        except git.exc.GitCommandError as ge:
            print(f"[GIT ERROR] Git command failed: {ge}")
            print(f"[GIT DEBUG] Command output: {ge.stderr}")
            return False
        except Exception as e:
            print(f"[GIT ERROR] Failed to update git: {e}")
            print(f"[GIT DEBUG] Exception type: {type(e).__name__}")
            import traceback
            print(f"[GIT DEBUG] Traceback: {traceback.format_exc()}")
            return False


# ------------------------------
# CONFIGURATION
# ------------------------------
# API KEY setup
API_KEY = "" # REPLACE THIS with your actual key
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL = "moonshotai/kimi-k2:free"

client = OpenAI(
  base_url="https://openrouter.ai/api/v1",
  api_key=API_KEY,
)

TARGET_COVERAGE = 100
MAX_ITERS = 3

# Project Paths (Based on your screenshots)
PROJECT_PATH = r"e:\ZC\Graduation Project\creator"
SPEC_PDF = Path(PROJECT_PATH) / "spec/OS_Creator_Spec.pdf"
UCDB_FILE = Path(PROJECT_PATH) / "covhtmlreport/coverage.ucdb"
COVERAGE_REPORT = Path(PROJECT_PATH) / "covhtmlreport/report.txt"
COMPILE_LIST = Path(PROJECT_PATH) / "compile_files.f"

# Context Files (The AI needs these to write valid code)

SEQ_ITEM_FILE = Path(PROJECT_PATH) / "creator_env/creator_sequence_item.svh"
BASE_SEQ_FILE = Path(PROJECT_PATH) / "creator_env/creator_sequence.svh" 
VIRT_SEQ_FILE = Path(PROJECT_PATH) / "creator_env/creator_virtual_sequence.svh"
SUBSCRIBER_FILE = Path(PROJECT_PATH) / "creator_env/creator_subscriber.svh"
TEST_BASE_FILE = Path(PROJECT_PATH) / "creator_env/creator_test_base.svh"



# Token tracking
token_stats = {
    "total_prompt_tokens": 0,
    "total_response_tokens": 0,
    "total_tokens": 0,
    "iterations": {}
}

# ------------------------------
# CORE LOGIC
# ------------------------------

def show_diff_and_confirm(original_file, new_code):
    """Show diff using available tools and wait for user confirmation."""
    import tempfile
    import difflib
    
    print("[DEBUG] Preparing diff for user review...")
    
    # Create temporary file with new code
    with tempfile.NamedTemporaryFile(mode='w', suffix='.svh', delete=False) as tmp:
        tmp.write(new_code)
        tmp_path = tmp.name
    
    print(f"[DEBUG] Temporary file created: {tmp_path}")
    print(f"[DEBUG] Original file: {original_file}")
    
    original_text = Path(original_file).read_text()
    
    # Try various diff tools in order of preference
    diff_tools = [
        ("VS Code", f'code --diff "{original_file}" "{tmp_path}"', True),  # shell=True for proper quoting
        ("WinMerge", ["WinMergeU", str(original_file), tmp_path], False),
        ("Beyond Compare", ["bcomp", str(original_file), tmp_path], False),
        ("Meld", ["meld", str(original_file), tmp_path], False),
    ]
    
    diff_opened = False
    for tool_name, cmd, use_shell in diff_tools:
        try:
            print(f"[INFO] Attempting to open diff with {tool_name}...")
            if use_shell:
                print(f"[DEBUG] Executing command: {cmd}")
                subprocess.run(cmd, shell=True, check=False)
            else:
                print(f"[DEBUG] Executing command: {cmd}")
                subprocess.run(cmd, check=False)
            print(f"[DEBUG] User closed {tool_name} window")
            diff_opened = True
            break
        except FileNotFoundError:
            print(f"[DEBUG] {tool_name} not found, trying next option...")
            continue
        except Exception as e:
            print(f"[DEBUG] {tool_name} failed: {e}")
            continue
    
    if not diff_opened:
        print("[WARNING] No GUI diff tool found. Showing text diff instead...")
        diff = list(difflib.unified_diff(original_text.splitlines(), new_code.splitlines(), lineterm=''))
        if diff:
            print("\n[DIFF] Changes to be applied:")
            print("\n".join(diff[:50]))  # Show first 50 lines
            if len(diff) > 50:
                print(f"... and {len(diff) - 50} more lines")
        else:
            print("[INFO] No changes detected")
    
    return tmp_path, True

def evolve_sequence(iteration, spec_text, cov_text):
    """Asks AI Model to rewrite the body of the virtual sequence."""
    
    current_vseq_code = VIRT_SEQ_FILE.read_text()
    # 1. Load Context (The "Vocabulary" of your UVM env)
    item_code = SEQ_ITEM_FILE.read_text()
    seq_code = BASE_SEQ_FILE.read_text()
    subscriber_code = SUBSCRIBER_FILE.read_text()
    base_test_code = TEST_BASE_FILE.read_text()
    
    prompt = f"""
    <role>
    You are a Senior UVM Verification Engineer specializing in Coverage Closure.
    Your task is to analyze functional coverage holes and evolve the stimulus to hit them.
    </role>
    
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

    <coverage_report_feedback>
    {cov_text}
    </coverage_report_feedback>

    <task_instructions>
    1. Analyze the <coverage_report_feedback> to identify the specific variable values or bins that are 'UNHIT'.
    2. Cross-reference these bins with the <uvm_transaction_item> to find which fields control that logic.
    3. Review the <specification_summary> to understand the intended functionality related to those coverage holes.
    4. REWRITE the `body()` task of the virtual sequence to generate directed stimulus for those holes.
    5. Ensure you use the existing sequencer handles and do not change the class name.
    6. Make sure to only add sequences to the available virtual sequence; do not create new classes.
    7. Make sure the code is complete and compilable.
    8. Keep the code style consistent with UVM best practices.
    9. Output the full, updated code of the file.
    </task_instructions>

    <output_format_requirement>
    Return ONLY a JSON object with this exact structure:
    {{
        "reasoning": "A brief explanation of which coverage holes you are targeting and how your new code hits them.",
        "updated_code": "// Full SystemVerilog Code here..."
    }}
    </output_format_requirement>
    """

    print(f"[AI] Evolving Virtual Sequence (Iteration {iteration})...")
    try:
        response = client.chat.completions.create(
          model=MODEL,
          messages=[
            {"role": "system", "content": "You are a UVM Expert."},
            {"role": "user", "content": prompt}
          ],
          # OpenRouter supports JSON mode for most top-tier models
          response_format={ "type": "json_object" } 
        )

        # Accessing content and token usage
        content = response.choices[0].message.content
        usage = response.usage
        
        print(f"[TOKEN USAGE] Prompt: {usage.prompt_tokens}, Completion: {usage.completion_tokens}")
        print(f"[COST] Approx: ${calculate_openrouter_cost(usage, MODEL):.6f}")
        
        # Return both the parsed code and token information
        result = json.loads(content)
        result['_token_info'] = {
            'prompt_tokens': usage.prompt_tokens,
            'response_tokens': usage.completion_tokens,
            'total_tokens': usage.total_tokens
        }
        return result
    except Exception as e:
        print(f"[ERROR] AI Update failed: {e}")
        return None

def calculate_openrouter_cost(usage, model_name):
    """
    Optional: OpenRouter provides pricing in their model list, 
    but for a quick estimate for Gemini Flash on OpenRouter:
    """
    p_rate = 0.10 / 1_000_000
    c_rate = 0.40 / 1_000_000
    return (usage.prompt_tokens * p_rate) + (usage.completion_tokens * c_rate)

def main():
    # 0. Backup original sequence
    backup_file = VIRT_SEQ_FILE.with_suffix(".svh.bak")
    if not backup_file.exists():
        shutil.copy(VIRT_SEQ_FILE, backup_file)
        print(f"[INFO] Created backup: {backup_file}")

    git_handler = GitManager(PROJECT_PATH)
    
    # Initial baseline simulation (before loop)
    print("[STEP] Running Initial Baseline Simulation...")
    print(f"[DEBUG] Simulation directory: {PROJECT_PATH}")
    print("[DEBUG] ========== INITIAL SIMULATION OUTPUT START ==========")
    sim_result = subprocess.run(
        ["vsim", "-c", "-do", "cov.do"], 
        cwd=PROJECT_PATH
    )
    print("[DEBUG] ========== INITIAL SIMULATION OUTPUT END ==========")
    print(f"[DEBUG] Simulation return code: {sim_result.returncode if sim_result else 'Unknown'}")
    if sim_result and sim_result.returncode != 0:
        print(f"[WARNING] Initial simulation failed with return code: {sim_result.returncode}")
        return

    # Get baseline coverage
    print("[DEBUG] Generating initial coverage report...")
    vcover_cmd = f'vcover report "{UCDB_FILE}" -details -output "{COVERAGE_REPORT}"'
    print(f"[DEBUG] vcover command: {vcover_cmd}")
    cov_result = subprocess.run(
        vcover_cmd,
        cwd=PROJECT_PATH,
        shell=True,
        capture_output=True,
        text=True
    )
    print(f"[DEBUG] vcover return code: {cov_result.returncode}")
    if cov_result.returncode != 0:
        print(f"[WARNING] vcover stderr: {cov_result.stderr}")
        return
    
    print(f"[DEBUG] Reading coverage report from: {COVERAGE_REPORT}")
    cov_text = COVERAGE_REPORT.read_text()
    match = re.search(r"TOTAL COVERGROUP COVERAGE\s*:\s*([\d\.]+)", cov_text)
    baseline_cov = float(match.group(1)) if match else 0.0
    print(f"[INFO] Baseline Coverage: {baseline_cov}%")
    
    # Main iteration loop
    for i in range(1, MAX_ITERS + 1):
        print(f"\n--- Iteration {i} ---")
        
        spec_text = "See attached PDF context" # Add PDF reading logic here if needed
        
        # 1. AI Update with diff preview
        print("[STEP] Generating AI improvements...")
        result = evolve_sequence(i, spec_text, cov_text)
        
        # Track tokens from this iteration
        if result and '_token_info' in result:
            token_info = result['_token_info']
            token_stats["iterations"][i] = token_info
            token_stats["total_prompt_tokens"] += token_info['prompt_tokens']
            token_stats["total_response_tokens"] += token_info['response_tokens']
            token_stats["total_tokens"] += token_info['total_tokens']
        
        if result and "updated_code" in result:
            print("\n[AI] Changes generated successfully.")
            print("[INFO] Opening diff viewer for review...")
            tmp_file, accepted = show_diff_and_confirm(VIRT_SEQ_FILE, result["updated_code"])
            
            print("\n[PROMPT] Do you want to implement these changes? (y/n): ", end="")
            user_choice = input().strip().lower()
            
            if user_choice == 'y' or user_choice == 'yes':
                # Overwrite the actual virtual sequence file
                VIRT_SEQ_FILE.write_text(result["updated_code"])
                print("[INFO] Virtual sequence updated.")
                
                print("[INFO] Recompiling...")
                # 4. Recompile
                compile_result = subprocess.run(["vlog", "-f", "compile_files.f"], cwd=PROJECT_PATH, capture_output=True, text=True)
                print(f"[DEBUG] Compilation return code: {compile_result.returncode}")
                if compile_result.returncode != 0:
                    print(f"[WARNING] Compilation failed: {compile_result.stderr}")
                else:
                    print("[STEP] Compilation successful. Running simulation with updated code...")
                    print("[DEBUG] ========== POST-UPDATE SIMULATION OUTPUT START ==========")
                    sim_result_post = subprocess.run(
                        ["vsim", "-c", "-do", "cov.do"], 
                        cwd=PROJECT_PATH
                    )
                    print("[DEBUG] ========== POST-UPDATE SIMULATION OUTPUT END ==========")
                    print(f"[DEBUG] Post-update simulation return code: {sim_result_post.returncode if sim_result_post else 'Unknown'}")
                    
                    if sim_result_post and sim_result_post.returncode == 0:
                        print("[STEP] Generating coverage report for updated code...")
                        vcover_cmd_post = f'vcover report "{UCDB_FILE}" -details -output "{COVERAGE_REPORT}"'
                        print(f"[DEBUG] vcover command: {vcover_cmd_post}")
                        cov_result_post = subprocess.run(
                            vcover_cmd_post,
                            cwd=PROJECT_PATH,
                            shell=True,
                            capture_output=True,
                            text=True
                        )
                        print(f"[DEBUG] vcover return code: {cov_result_post.returncode}")
                        if cov_result_post.returncode == 0:
                            cov_text_post = COVERAGE_REPORT.read_text()
                            match_post = re.search(r"TOTAL COVERGROUP COVERAGE\s*:\s*([\d\.]+)", cov_text_post)
                            new_cov = float(match_post.group(1)) if match_post else 0.0
                            print(f"[INFO] Coverage after updates: {new_cov}%")
                            print(f"[INFO] Coverage improvement: {new_cov - baseline_cov:+.2f}%")
                            
                            # Now commit and push with new coverage numbers
                            print(f"[DEBUG] Calling commit_and_push with iteration={i}, new coverage={new_cov}%")
                            push_success = git_handler.commit_and_push(i, new_cov)
                            print(f"[DEBUG] Git push result: {push_success}")
                            
                            if new_cov >= TARGET_COVERAGE:
                                print(f"[SUCCESS] Target coverage of {TARGET_COVERAGE}% reached!")
                                break
                        else:
                            print(f"[WARNING] Post-update vcover failed: {cov_result_post.stderr}")
                    else:
                        print("[WARNING] Post-update simulation failed")
            else:
                print("[INFO] User rejected changes. Skipping update for this iteration.")
            
            # Cleanup temporary file
            try:
                Path(tmp_file).unlink()
                print(f"[DEBUG] Cleaned up temporary file: {tmp_file}")
            except:
                pass
        else:
            print("[WARNING] AI failed to generate updated code")
        
        time.sleep(5)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Script interrupted by user.")
    except Exception as e:
        print(f"[ERROR] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Print token usage summary
        print("\n" + "="*60)
        print("[TOKEN USAGE SUMMARY]")
        print("="*60)
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
        print("="*60)
        
        print("\n[INFO] Cleaning up and exiting...")
        # Force garbage collection to ensure subprocess handles are closed
        import gc
        gc.collect()