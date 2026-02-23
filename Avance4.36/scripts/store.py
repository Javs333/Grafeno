import sys
import os
import json
import uuid

def main():
    if len(sys.argv) < 2:
        print("Usage: store.py <run_dir>")
        sys.exit(1)

    run_dir = sys.argv[1]
    run_id = os.path.basename(run_dir)
    
    print(f"[Storage] Aggregating results for {run_id}")
    
    # In a real scenario, we would read the files and INSERT into Postgres
    # import psycopg2...
    
    # For now, just print what we would store
    files = os.listdir(run_dir)
    print(f"Found artifacts: {files}")
    print("Inserted into database successfully (simulated).")

if __name__ == "__main__":
    main()
