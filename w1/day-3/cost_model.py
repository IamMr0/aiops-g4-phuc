class CostModel:
    def __init__(self):
        # Self-Hosted Assumptions (Monthly)
        self.compute_unit_cost = 50.0  # $ per vCPU/month (approximate average)
        self.storage_hot_cost = 0.08   # $ per GB (SSD for metrics/recent logs)
        self.storage_cold_cost = 0.023 # $ per GB (S3/Object storage for logs)
        self.network_cost_per_gb = 0.01 # $ per GB cross-AZ/egress traffic
        
        # Datadog SaaS Pricing Assumptions
        self.dd_host_cost = 15.0       # $ per host/month
        self.dd_log_ingest = 0.10      # $ per GB logs ingested
        self.dd_metric_cost = 0.05     # $ per custom metric time-series
        
    def estimate_self_hosted(self, tier, logs_gb_day, metrics_eps):
        logs_gb_month = logs_gb_day * 30
        
        # Compute requirements (heuristic based on scale)
        if tier == "Small":
            vcpus = 8
        elif tier == "Medium":
            vcpus = 64
        else:
            vcpus = 512
            
        compute_cost = vcpus * self.compute_unit_cost
        
        # Storage requirements
        # Metrics: Keep 30 days hot. Roughly 1 byte per event compressed.
        metrics_gb_month = (metrics_eps * 86400 * 30 * 1) / (1024**3)
        hot_storage_cost = metrics_gb_month * self.storage_hot_cost
        cold_storage_cost = logs_gb_month * self.storage_cold_cost
        storage_cost = hot_storage_cost + cold_storage_cost
        
        # Network requirements (internal traffic)
        network_cost = (logs_gb_month + metrics_gb_month) * self.network_cost_per_gb
        
        total_cost = compute_cost + storage_cost + network_cost
        return {
            "Tier": tier,
            "Compute ($)": round(compute_cost, 2),
            "Storage ($)": round(storage_cost, 2),
            "Network ($)": round(network_cost, 2),
            "Total Self-Hosted ($)": round(total_cost, 2)
        }

    def estimate_datadog(self, tier, services, logs_gb_day, metrics_eps):
        logs_gb_month = logs_gb_day * 30
        
        # Assume 1 service = 2 hosts minimum, scaled up
        if tier == "Small":
            hosts = 20
        elif tier == "Medium":
            hosts = 200
        else:
            hosts = 2000
            
        infra_cost = hosts * self.dd_host_cost
        logs_cost = logs_gb_month * self.dd_log_ingest
        
        # Assume 1 eps roughly equals 1 custom metric time-series
        metrics_cost = metrics_eps * self.dd_metric_cost
        
        total_cost = infra_cost + logs_cost + metrics_cost
        return {
            "Infra ($)": round(infra_cost, 2),
            "Logs ($)": round(logs_cost, 2),
            "Metrics ($)": round(metrics_cost, 2),
            "Total Datadog ($)": round(total_cost, 2)
        }

def main():
    tiers = [
        {"tier": "Small", "services": 10, "logs_gb": 50, "metrics_eps": 100_000},
        {"tier": "Medium", "services": 100, "logs_gb": 500, "metrics_eps": 1_000_000},
        {"tier": "Large", "services": 1000, "logs_gb": 5000, "metrics_eps": 10_000_000},
    ]
    
    model = CostModel()
    
    print(f"{'Tier':<10} | {'Self-Hosted Compute':<20} | {'Self-Hosted Storage':<20} | {'Self-Hosted Network':<20} | {'Total Self-Hosted':<20} | {'Total Datadog (SaaS)':<20}")
    print("-" * 120)
    
    for t in tiers:
        sh = model.estimate_self_hosted(t["tier"], t["logs_gb"], t["metrics_eps"])
        dd = model.estimate_datadog(t["tier"], t["services"], t["logs_gb"], t["metrics_eps"])
        
        print(f"{t['tier']:<10} | ${sh['Compute ($)']:<19} | ${sh['Storage ($)']:<19} | ${sh['Network ($)']:<19} | ${sh['Total Self-Hosted ($)']:<19} | ${dd['Total Datadog ($)']:<20}")

if __name__ == "__main__":
    main()
