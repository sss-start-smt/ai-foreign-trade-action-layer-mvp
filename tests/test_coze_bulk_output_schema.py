import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v61_extensions import _coze_safe_bulk_update_response


def test_coze_bulk_update_values_are_strings_without_mutating_core_result():
    core = {
        "batch_id": "BUP-1",
        "orders": [
            {
                "order_id": "ORD-1",
                "updates": [
                    {
                        "field_name": "current_progress",
                        "old_value": 0.45,
                        "new_value": 0.82,
                        "new_value_text": "82%",
                    },
                    {
                        "field_name": "requested_delivery_date",
                        "old_value": None,
                        "new_value": "2026-08-20",
                        "new_value_text": "2026-08-20",
                    },
                ],
            }
        ],
    }

    safe = _coze_safe_bulk_update_response(core)
    updates = safe["orders"][0]["updates"]

    assert updates[0]["old_value"] == "45%"
    assert updates[0]["new_value"] == "82%"
    assert updates[1]["old_value"] == ""
    assert updates[1]["new_value"] == "2026-08-20"

    # The website/writeback path keeps typed values.
    assert core["orders"][0]["updates"][0]["old_value"] == 0.45
    assert core["orders"][0]["updates"][0]["new_value"] == 0.82
