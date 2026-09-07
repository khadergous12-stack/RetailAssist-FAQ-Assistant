"""Generate reproducible synthetic support-ticket data for SupportAI analytics."""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path


SEED = 42
RECORD_COUNT = 500
OUTPUT_PATH = Path(__file__).with_name("support_tickets.csv")

# The assignment was issued in August 2026, so the 12 complete months are
# September 2025 through August 2026.
START_DATE = date(2025, 9, 1)
END_DATE = date(2026, 8, 31)

CATEGORIES = [
    ("Refunds", 0.25),
    ("Shipping", 0.25),
    ("Returns", 0.20),
    ("Payments", 0.15),
    ("Warranty", 0.15),
]

STATUSES = [
    ("Closed", 0.70),
    ("In Progress", 0.20),
    ("Open", 0.10),
]

PRIORITIES = [
    ("Low", 0.20),
    ("Medium", 0.45),
    ("High", 0.28),
    ("Critical", 0.07),
]

CHANNELS = ["Email", "Chat", "Phone", "Web"]
REGIONS = ["North", "South", "East", "West"]
PRODUCT_FAMILIES = ["Electronics", "Home", "Fashion", "Beauty", "Sports"]

# Refunds are deliberately slower so category comparisons are meaningful.
CATEGORY_BASE_HOURS = {
    "Refunds": 30,
    "Shipping": 16,
    "Returns": 12,
    "Payments": 18,
    "Warranty": 22,
}

PRIORITY_ADD_HOURS = {
    "Low": 0,
    "Medium": 3,
    "High": 8,
    "Critical": 15,
}


def weighted_choice(items: list[tuple[str, float]]) -> str:
    """Choose one value according to the supplied weights."""
    values = [value for value, _ in items]
    weights = [weight for _, weight in items]
    return random.choices(values, weights=weights, k=1)[0]


def weighted_population(
    items: list[tuple[str, float]],
    count: int,
) -> list[str]:
    """Build an exact-size population that follows the requested weights."""
    population: list[str] = []

    for value, weight in items:
        population.extend([value] * int(count * weight))

    # The configured percentages sum to 100%, so this should normally be
    # exact. Fill any rounding gap defensively.
    while len(population) < count:
        population.append(items[len(population) % len(items)][0])

    return population[:count]


def random_date(start: date, end: date) -> date:
    """Return a random date between start and end, inclusive."""
    span = (end - start).days
    return start + timedelta(days=random.randint(0, span))


def resolution_hours(category: str, priority: str) -> float:
    """Generate category- and priority-sensitive resolution time."""
    base = CATEGORY_BASE_HOURS[category]
    priority_add = PRIORITY_ADD_HOURS[priority]
    return round(max(1.0, random.gauss(base + priority_add, 7)), 1)


def escalation_flag(priority: str, resolution_time: float | None) -> bool:
    """Make escalation more likely for severe or slow tickets."""
    probability = {
        "Low": 0.04,
        "Medium": 0.10,
        "High": 0.28,
        "Critical": 0.55,
    }[priority]

    if resolution_time is not None and resolution_time >= 36:
        probability += 0.15
    elif resolution_time is not None and resolution_time >= 28:
        probability += 0.08

    return random.random() < min(probability, 0.90)


def customer_rating(
    resolution_time: float | None,
    escalated: bool,
) -> int | None:
    """Generate a 1-5 rating, lower for slow or escalated tickets."""
    if resolution_time is None:
        return None

    score = 4.6

    if resolution_time >= 36:
        score -= 1.8
    elif resolution_time >= 28:
        score -= 1.0
    elif resolution_time >= 20:
        score -= 0.4

    if escalated:
        score -= 0.7

    # Small random variation keeps the dataset realistic while preserving
    # the intended negative relationship.
    score += random.gauss(0, 0.55)
    return max(1, min(5, int(round(score))))


def refund_amount(category: str) -> float:
    """Generate mostly non-zero refund amounts for Refunds/Returns."""
    if category in {"Refunds", "Returns"}:
        # Most such tickets have a refund, but a minority are zero.
        if random.random() < 0.88:
            return round(random.uniform(15, 450), 2)
        return 0.00

    return 0.00


def generate_rows(record_count: int = RECORD_COUNT) -> list[dict[str, object]]:
    """Generate synthetic ticket rows with reproducible relationships."""
    rows: list[dict[str, object]] = []

    # Use exact category/status/priority proportions so the generated
    # dataset matches the assignment's requested distribution.
    categories = weighted_population(CATEGORIES, record_count)
    statuses = weighted_population(STATUSES, record_count)
    priorities = weighted_population(PRIORITIES, record_count)

    random.shuffle(categories)
    random.shuffle(statuses)
    random.shuffle(priorities)

    for number in range(1, record_count + 1):
        category = categories[number - 1]
        status = statuses[number - 1]
        priority = priorities[number - 1]

        # Only resolved/closed tickets receive resolution time and rating.
        resolved = status == "Closed"
        resolution_time = (
            resolution_hours(category, priority) if resolved else None
        )

        escalated = escalation_flag(priority, resolution_time)
        rating = customer_rating(resolution_time, escalated)

        rows.append(
            {
                "ticket_id": f"T{number:06d}",
                "created_date": random_date(START_DATE, END_DATE).isoformat(),
                "category": category,
                "status": status,
                "priority": priority,
                "resolution_time_hours": resolution_time,
                "customer_rating": rating,
                "channel": random.choice(CHANNELS),
                "region": random.choice(REGIONS),
                "product_family": random.choice(PRODUCT_FAMILIES),
                "refund_amount": refund_amount(category),
                "is_escalated": escalated,
            }
        )

    return rows


def write_csv(rows: list[dict[str, object]], output_path: Path = OUTPUT_PATH) -> None:
    """Write generated ticket rows to CSV."""
    fieldnames = [
        "ticket_id",
        "created_date",
        "category",
        "status",
        "priority",
        "resolution_time_hours",
        "customer_rating",
        "channel",
        "region",
        "product_family",
        "refund_amount",
        "is_escalated",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    """Generate and save the support-ticket dataset."""
    random.seed(SEED)
    rows = generate_rows()
    write_csv(rows)
    print(f"Generated {len(rows)} support tickets: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
