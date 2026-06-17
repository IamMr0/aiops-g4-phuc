#!/usr/bin/env python3
"""Break-even cost model for an AIOps platform (§8)."""

def is_worth_it(
    num_services: int,
    incidents_per_month: int,
    avg_incident_duration_hours: float,
    downtime_cost_per_hour: float,
    expected_mttr_reduction_pct: float = 0.4,
    aiops_monthly_cost: float = 15_000,
) -> dict:
    """
    Returns:
      {
        "monthly_value": float,
        "monthly_cost": float,
        "roi": float,
        "payback_months": float,  # or float('inf')
        "verdict": "worth_it" | "marginal" | "not_worth_it"
      }
    Verdict rule:
      roi > 1.5  → worth_it
      1.0 < roi <= 1.5 → marginal
      roi <= 1.0 → not_worth_it
    """
    monthly_downtime_hours = incidents_per_month * avg_incident_duration_hours
    monthly_value = (
        monthly_downtime_hours
        * expected_mttr_reduction_pct
        * downtime_cost_per_hour
    )
    roi = monthly_value / aiops_monthly_cost
    payback_months = aiops_monthly_cost / monthly_value if monthly_value > 0 else float('inf')
    
    if roi > 1.5:
        verdict = "worth_it"
    elif roi > 1.0:
        verdict = "marginal"
    else:
        verdict = "not_worth_it"
    
    return {
        "monthly_value": monthly_value,
        "monthly_cost": aiops_monthly_cost,
        "roi": roi,
        "payback_months": payback_months,
        "verdict": verdict
    }


if __name__ == "__main__":
    print(is_worth_it(num_services=20, incidents_per_month=2,
                      avg_incident_duration_hours=1, downtime_cost_per_hour=10_000,
                      aiops_monthly_cost=15_000))
    print(is_worth_it(num_services=100, incidents_per_month=5,
                      avg_incident_duration_hours=2, downtime_cost_per_hour=20_000,
                      aiops_monthly_cost=25_000))
    # E-commerce scenario: 500 services, high downtime cost
    # Downtime cost: $50k/hour (mid-tier e-commerce)
    print(is_worth_it(num_services=500, incidents_per_month=10,
                      avg_incident_duration_hours=1.5, downtime_cost_per_hour=50_000,
                      aiops_monthly_cost=60_000))
