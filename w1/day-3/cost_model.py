class CostModel:
    """
    Monthly cost estimation for an AIOps observability platform.

    Tiers:
    - Small: 10 services, 50 GB logs/day, 100K metric events/sec
    - Medium: 100 services, 500 GB logs/day, 1M metric events/sec
    - Large: 1000 services, 5 TB logs/day, 10M metric events/sec
    """

    def __init__(self):
        # ----------------------------
        # Self-hosted assumptions
        # ----------------------------
        self.vcpu_cost = 50.0          # $ / vCPU / month
        self.storage_cost = 0.023      # $ / GB / month (object storage)
        self.network_cost = 0.01       # $ / GB

        # ----------------------------
        # Datadog assumptions
        # Public list pricing (approx)
        # ----------------------------
        self.dd_infra_host = 15.0      # Infrastructure Monitoring
        self.dd_apm_host = 31.0        # APM
        self.dd_log_ingest = 0.10      # Log ingestion per GB

    # ------------------------------------------------
    # Self-hosted estimate
    # ------------------------------------------------
    def estimate_self_hosted(
        self,
        tier,
        services,
        logs_gb_day,
        metrics_eps
    ):
        logs_gb_month = logs_gb_day * 30

        if tier == "Small":
            vcpus = 8
        elif tier == "Medium":
            vcpus = 64
        else:
            vcpus = 512

        compute_cost = vcpus * self.vcpu_cost

        storage_cost = logs_gb_month * self.storage_cost

        # simple network estimate:
        # assume monthly network traffic ≈ 2x ingested logs
        network_gb = logs_gb_month * 2
        network_cost = network_gb * self.network_cost

        total = (
            compute_cost
            + storage_cost
            + network_cost
        )

        return {
            "Tier": tier,
            "Compute": round(compute_cost, 2),
            "Storage": round(storage_cost, 2),
            "Network": round(network_cost, 2),
            "Total": round(total, 2)
        }

    # ------------------------------------------------
    # Datadog estimate
    # ------------------------------------------------
    def estimate_datadog(
        self,
        tier,
        services,
        logs_gb_day
    ):
        logs_gb_month = logs_gb_day * 30

        # sizing assumption:
        # ~2 hosts per service
        hosts = services * 2

        infra_cost = hosts * self.dd_infra_host
        apm_cost = hosts * self.dd_apm_host
        logs_cost = logs_gb_month * self.dd_log_ingest

        total = (
            infra_cost
            + apm_cost
            + logs_cost
        )

        return {
            "Infra": round(infra_cost, 2),
            "APM": round(apm_cost, 2),
            "Logs": round(logs_cost, 2),
            "Total": round(total, 2)
        }


def print_breakdown(tier, self_hosted, datadog):
    print(f"\n{'=' * 70}")
    print(f"{tier} TIER")
    print(f"{'=' * 70}")

    print("\nSELF-HOSTED")
    print(f"  Compute : ${self_hosted['Compute']:,.2f}")
    print(f"  Storage : ${self_hosted['Storage']:,.2f}")
    print(f"  Network : ${self_hosted['Network']:,.2f}")
    print(f"  TOTAL   : ${self_hosted['Total']:,.2f}")

    print("\nDATADOG")
    print(f"  Infra   : ${datadog['Infra']:,.2f}")
    print(f"  APM     : ${datadog['APM']:,.2f}")
    print(f"  Logs    : ${datadog['Logs']:,.2f}")
    print(f"  TOTAL   : ${datadog['Total']:,.2f}")

    savings = datadog["Total"] - self_hosted["Total"]

    print("\nBUILD VS BUY")
    if savings > 0:
        print(
            f"  Self-hosted saves approximately "
            f"${savings:,.2f}/month"
        )
    else:
        print(
            f"  Datadog saves approximately "
            f"${abs(savings):,.2f}/month"
        )


def main():
    tiers = [
        {
            "tier": "Small",
            "services": 10,
            "logs_gb_day": 50,
            "metrics_eps": 100_000
        },
        {
            "tier": "Medium",
            "services": 100,
            "logs_gb_day": 500,
            "metrics_eps": 1_000_000
        },
        {
            "tier": "Large",
            "services": 1000,
            "logs_gb_day": 5000,
            "metrics_eps": 10_000_000
        }
    ]

    model = CostModel()

    for t in tiers:
        self_hosted = model.estimate_self_hosted(
            t["tier"],
            t["services"],
            t["logs_gb_day"],
            t["metrics_eps"]
        )

        datadog = model.estimate_datadog(
            t["tier"],
            t["services"],
            t["logs_gb_day"]
        )

        print_breakdown(
            t["tier"],
            self_hosted,
            datadog
        )


if __name__ == "__main__":
    main()
