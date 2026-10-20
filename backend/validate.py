#!/usr/bin/env python3
"""
Simple validation script to test Sentinel components without pytest issues.
"""
from app.services.generator import SyntheticDataGenerator
from datetime import datetime

def test_generator():
    """Test synthetic data generator."""
    print("Testing synthetic data generator...")
    generator = SyntheticDataGenerator()

    # Test wash trade generation
    wash_events = generator.generate_wash_trade()
    assert len(wash_events) > 0
    print(f"✓ Generated {len(wash_events)} wash trade events")

    # Test spoofing generation
    spoof_events = generator.generate_spoofing()
    assert len(spoof_events) > 0
    print(f"✓ Generated {len(spoof_events)} spoofing events")

    # Test normal trading
    normal_events = generator.generate_normal_trading(count=20)
    assert len(normal_events) > 0
    print(f"✓ Generated {len(normal_events)} normal trading events")

    # Test mixed scenario
    mixed_events = generator.generate_scenario("mixed")
    assert len(mixed_events) > 50
    print(f"✓ Generated {len(mixed_events)} mixed scenario events")

    print("\n✅ All generator tests passed!")

if __name__ == "__main__":
    test_generator()
