import subprocess
import os
import sys
import glob
from datetime import datetime
from pathlib import Path
import supabase_export

def main():
    mode = os.environ.get("RUN_MODE", "test").lower()
    triggered_by = os.environ.get("TRIGGERED_BY", "job")
    
    # Generate run ID
    run_id = os.environ.get("RUN_ID") or datetime.now().strftime("%Y%m%d_%H%M")
    print(f"=== Starting Helokn Lead Generation Pipeline ===")
    print(f"Run ID:       {run_id}")
    print(f"Mode:         {mode}")
    print(f"Triggered by: {triggered_by}")
    print(f"=================================================")

    # Prepare command to execute run.py
    # By default, run.py runs in test mode. If mode is "full", we add --full
    cmd = ["python", "run.py"]
    if mode == "full":
        cmd.append("--full")
        
    print(f"Running pipeline command: {' '.join(cmd)}")
    
    # Run the subprocess
    result = subprocess.run(cmd, check=False)
    
    if result.returncode != 0:
        print(f"Error: Pipeline run failed with exit code {result.returncode}")
        # Upsert status as failed in Supabase pipeline_runs
        try:
            sb = supabase_export.get_client()
            sb.table("pipeline_runs").upsert({
                "run_id": run_id,
                "status": "failed",
                "triggered_by": triggered_by,
                "leads_count": 0,
                "rejects_count": 0,
                "notes": f"Process exited with code {result.returncode}"
            }).execute()
            print("Successfully recorded run failure in Supabase.")
        except Exception as e:
            print(f"Failed to record run failure in Supabase: {e}")
        
        sys.exit(result.returncode)
        
    print("Pipeline run completed successfully. Preparing to export results to Supabase...")
    
    # Locate output CSV files
    base_dir = Path(__file__).resolve().parent
    output_dir = base_dir / "output"
    
    leads_csv = output_dir / "helokn_leads.csv"
    rejects_csv = output_dir / "rejected_candidates.csv"
    
    if not leads_csv.exists() or not rejects_csv.exists():
        print(f"Warning: Stable CSV files not found. Searching for timestamped CSVs...")
        # Fall back to timestamped files if they exist
        lead_files = sorted(glob.glob(str(output_dir / "helokn_leads_*.csv")))
        reject_files = sorted(glob.glob(str(output_dir / "rejected_candidates_*.csv")))
        if lead_files and reject_files:
            leads_csv = Path(lead_files[-1])
            rejects_csv = Path(reject_files[-1])
            print(f"Found timestamped CSVs: {leads_csv.name}, {rejects_csv.name}")
        else:
            print(f"Error: No output CSV files found in {output_dir}")
            sys.exit(1)
            
    print(f"Uploading leads from: {leads_csv}")
    print(f"Uploading rejects from: {rejects_csv}")
    
    try:
        supabase_export.push_run(
            leads_csv=str(leads_csv),
            rejects_csv=str(rejects_csv),
            run_id=run_id,
            triggered_by=triggered_by
        )
        print("Data successfully uploaded to Supabase.")
    except Exception as e:
        print(f"Error pushing data to Supabase: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
