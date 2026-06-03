import os
import csv
import json
import threading
import queue
import statistics
from collections import deque

# Config
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(BASE_DIR, "data", "machine_temperature_system_failure.csv")
OUTPUT_JSON = os.path.join(BASE_DIR, "output", "features.json")
OUTPUT_PARQUET = os.path.join(BASE_DIR, "output", "features.parquet")
WINDOW_SIZE = 5

def producer(q: queue.Queue, filepath: str):
    """Reads from CSV and emits rows to the queue (mock Kafka producer)."""
    print(f"[Producer] Starting to read from {filepath}")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            count = 0
            for row in reader:
                q.put({
                    "timestamp": row["timestamp"],
                    "value": float(row["value"])
                })
                count += 1
            print(f"[Producer] Successfully pushed {count} records to queue.")
    except FileNotFoundError:
        print(f"[Producer] Error: File {filepath} not found.")
    
    # Signal the consumer that we are done
    q.put(None)
    print("[Producer] Finished reading data.")

def consumer(q: queue.Queue, output_json: str, output_parquet: str):
    """Reads from queue, extracts features, and saves output (mock Flink/Spark)."""
    print("[Consumer] Starting to process stream...")
    window = deque(maxlen=WINDOW_SIZE)
    features_list = []
    
    while True:
        data_point = q.get()
        if data_point is None:
            break
            
        current_value = data_point["value"]
        window.append(current_value)
        
        feature = {
            "timestamp": data_point["timestamp"],
            "raw_value": current_value,
            "rolling_mean": None,
            "rolling_std": None,
            "rate_of_change": None
        }
        
        if len(window) == WINDOW_SIZE:
            feature["rolling_mean"] = statistics.mean(window)
            feature["rolling_std"] = statistics.stdev(window) if WINDOW_SIZE > 1 else 0.0
            feature["rate_of_change"] = (window[-1] - window[0]) / WINDOW_SIZE
            
        features_list.append(feature)
        
    print(f"[Consumer] Processing complete. Processed {len(features_list)} records.")
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_parquet), exist_ok=True)
    
    # Save to Parquet using Pandas
    try:
        import pandas as pd
        df = pd.DataFrame(features_list)
        df.to_parquet(output_parquet)
        print(f"[Consumer] Features successfully saved to {output_parquet}")
    except ImportError:
        print("[Consumer] Pandas/PyArrow not found. Skipping Parquet export.")
        
    # Save to JSON
    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(features_list, f, indent=4)
    print(f"[Consumer] Features successfully saved to {output_json}")

def main():
    # We use a Queue to simulate Kafka/PubSub with a small buffer
    q = queue.Queue(maxsize=1000)
    
    # Bonus: Using threading to run producer and consumer concurrently
    producer_thread = threading.Thread(target=producer, args=(q, CSV_FILE))
    consumer_thread = threading.Thread(target=consumer, args=(q, OUTPUT_JSON, OUTPUT_PARQUET))
    
    # Start threads
    producer_thread.start()
    consumer_thread.start()
    
    # Wait for completion
    producer_thread.join()
    consumer_thread.join()
    
    print("Pipeline execution completed successfully.")

if __name__ == "__main__":
    main()
