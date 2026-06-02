import sys
import os
import pandas as pd
import re
from datetime import datetime, timedelta
import drain3
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

def parse_time(line):
    # Optimized timestamp parsing for HDFS (e.g., 081109 203518)
    # This avoids slow datetime.strptime overhead for 11M lines.
    if len(line) >= 13 and line[6] == ' ' and line[:6].isdigit():
        try:
            return datetime(
                year=2000 + int(line[0:2]),
                month=int(line[2:4]),
                day=int(line[4:6]),
                hour=int(line[7:9]),
                minute=int(line[9:11]),
                second=int(line[11:13])
            )
        except ValueError:
            pass

    # BGL: YYYY-MM-DD-HH.MM.SS
    match_bgl = re.search(r'(\d{4}-\d{2}-\d{2}-\d{2}\.\d{2}\.\d{2})', line)
    if match_bgl:
        try:
            return datetime.strptime(match_bgl.group(1), '%Y-%m-%d-%H.%M.%S')
        except ValueError:
            pass
    return None

def main():
    if len(sys.argv) < 2:
        print("Usage: python log_analyzer.py <logfile>")
        sys.exit(1)
        
    log_file = sys.argv[1]
    if not os.path.exists(log_file):
        print(f"File not found: {log_file}")
        sys.exit(1)

    print(f"Analyzing {log_file}...")
    
    config = TemplateMinerConfig()
    try:
        config.load(os.path.join(os.path.dirname(drain3.__file__), "drain3.ini"))
    except:
        pass # fallback to default if ini not found
    config.profiling_enabled = False
    miner = TemplateMiner(config=config)

    logs = []
    
    print("Parsing logs with Drain3...")
    with open(log_file, 'r', encoding='utf8', errors='ignore') as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line: continue
            
            dt = parse_time(line)
            cluster = miner.add_log_message(line)
            
            if dt:
                # Handle both dict and object depending on Drain3 version
                t_id = cluster['cluster_id'] if isinstance(cluster, dict) else cluster.cluster_id
                logs.append({'time': dt, 'template_id': t_id})
                
            if (i+1) % 500000 == 0:
                print(f"  Processed {i+1} lines...")

    total_lines = i + 1
    clusters = miner.drain.clusters
    num_unique_templates = len(clusters)
    
    print(f"\n--- OVERVIEW ---")
    print(f"Total lines: {total_lines}")
    print(f"Unique templates: {num_unique_templates}")
    
    # Top 5
    templates_data = [{'id': c.cluster_id, 'template': c.get_template(), 'count': c.size} for c in clusters]
    df_templates = pd.DataFrame(templates_data).sort_values('count', ascending=False)
    
    print(f"\n--- TOP-5 TEMPLATES ---")
    for idx, row in df_templates.head(5).iterrows():
        pct = (row['count'] / total_lines) * 100
        print(f"ID {row['id']} | Count: {row['count']} ({pct:.2f}%) | {row['template'][:80]}...")

    if not logs:
        print("\nCould not parse timestamps, skipping time-series analysis.")
        return

    df_logs = pd.DataFrame(logs)
    max_time = df_logs['time'].max()
    min_time = df_logs['time'].min()
    
    print(f"\n--- TIME ANALYSIS ---")
    print(f"Time range: {min_time} to {max_time}")
    
    last_hour = max_time - timedelta(hours=1)
    
    history_logs = df_logs[df_logs['time'] < last_hour]
    recent_logs = df_logs[df_logs['time'] >= last_hour]
    
    # New templates in the last 1 hour
    history_templates = set(history_logs['template_id'].unique())
    recent_templates = set(recent_logs['template_id'].unique())
    new_templates = recent_templates - history_templates
    
    print(f"\nNew templates (not appeared before the last hour): {len(new_templates)}")
    for t_id in new_templates:
        t_str = next(c.get_template() for c in clusters if c.cluster_id == t_id)
        print(f" - ID {t_id}: {t_str[:80]}...")
        
    # Spiking templates
    print("\nTemplates spiking in the last hour (compared to average):")
    history_counts = history_logs.groupby('template_id').size()
    recent_counts = recent_logs.groupby('template_id').size()
    
    total_history_hours = max((last_hour - min_time).total_seconds() / 3600, 1)
    history_avg_per_hour = history_counts / total_history_hours
    
    spikes = []
    for t_id, recent_count in recent_counts.items():
        avg_count = history_avg_per_hour.get(t_id, 0)
        if recent_count > 10 and recent_count > avg_count * 3:
            spikes.append((t_id, recent_count, avg_count))
            
    spikes.sort(key=lambda x: x[1]/max(x[2], 1), reverse=True)
    for t_id, rc, ac in spikes[:5]:
        t_str = next(c.get_template() for c in clusters if c.cluster_id == t_id)
        print(f" - ID {t_id}: {rc} times (Average {ac:.2f} times/hour) | {t_str[:80]}...")

if __name__ == '__main__':
    main()
