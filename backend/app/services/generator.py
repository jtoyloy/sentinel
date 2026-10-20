"""Seeded event-time replay scenarios with complete order lifecycles."""
from datetime import datetime, timedelta, timezone
import random
from typing import Dict, List, Optional


class SyntheticDataGenerator:
    INSTRUMENTS = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM"]
    TRADERS = [f"T{i:03d}" for i in range(1, 21)]
    ACCOUNTS = [f"ACC{i:03d}" for i in range(1, 31)]
    FIRMS = ["FIRM_A", "FIRM_B", "FIRM_C", "FIRM_D"]
    PRICES = dict(zip(INSTRUMENTS, [230, 430, 175, 195, 250, 580, 135, 220]))

    def __init__(self, base_time: Optional[datetime] = None, seed: int = 42):
        self.base_time = base_time or datetime.now(timezone.utc)
        if self.base_time.tzinfo is None or self.base_time.utcoffset() is None:
            raise ValueError("base_time must include a timezone")
        self.random = random.Random(seed)
        self.order_counter = 1000
        self._normal_counter = 0
        self._cursor = self.base_time

    def _next_order_id(self):
        self.order_counter += 1
        return f"ORD_{self.order_counter}"

    def _event(self, kind, timestamp, instrument, trader, account, firm, side, quantity, price, order_id, **extra):
        metadata = {"source": "synthetic", "currency": "USD", **extra.pop("metadata", {})}
        return dict(event_type=kind, timestamp=timestamp.isoformat(), instrument_id=instrument,
                    trader_id=trader, account_id=account, firm_id=firm, side=side,
                    quantity=quantity, price=round(price, 2), order_id=order_id, metadata=metadata, **extra)

    def generate_normal_trading(self, count: int = 50) -> List[Dict]:
        events = []
        for _ in range(count):
            self._normal_counter += 1
            instrument = self.random.choice(self.INSTRUMENTS)
            trader = self.random.choice(self.TRADERS)
            # Independent beneficial owners prevent artificial wash signals in the control scenario.
            account = f"NORMAL_{self._normal_counter:05d}"
            owner = f"CLIENT_{self._normal_counter:05d}"
            firm = self.random.choice(self.FIRMS)
            side = self.random.choice(["BUY", "SELL"])
            quantity = self.random.randint(1, 80) * 100
            price = round(self.PRICES[instrument] * self.random.uniform(0.998, 1.002), 2)
            order_id = self._next_order_id()
            args = (instrument, trader, account, firm, side, quantity, price, order_id)
            events.append(self._event("ORDER", self._cursor, *args,
                                      metadata={"order_type": "LIMIT", "beneficial_owner": owner}))
            delay = self.random.randint(1, 12)
            kind = "EXECUTION" if self.random.random() < 0.8 else "CANCEL"
            extra = {"parent_order_id": order_id} if kind == "CANCEL" else {}
            events.append(self._event(kind, self._cursor + timedelta(seconds=delay), *args,
                                      metadata={"beneficial_owner": owner, "execution_venue": "NASDAQ"} if kind == "EXECUTION"
                                      else {"beneficial_owner": owner, "cancel_reason": "client_request"}, **extra))
            self._cursor += timedelta(seconds=self.random.randint(3, 10))
        return sorted(events, key=lambda event: event["timestamp"])

    def generate_wash_trade(self) -> List[Dict]:
        instrument = self.random.choice(self.INSTRUMENTS)
        trader = self.random.choice(self.TRADERS)
        quantity = self.random.randint(10, 50) * 100
        price = round(self.PRICES[instrument] * self.random.uniform(0.999, 1.001), 2)
        events = []
        for side, account, offset in (("BUY", "WASH_ACC_A", 0), ("SELL", "WASH_ACC_B", 0.1)):
            order_id = self._next_order_id()
            args = (instrument, trader, account, "FIRM_A", side, quantity, price, order_id)
            events.append(self._event("ORDER", self._cursor + timedelta(seconds=offset), *args,
                                      metadata={"beneficial_owner": "ENTITY_X", "order_type": "LIMIT"}))
            events.append(self._event("EXECUTION", self._cursor + timedelta(seconds=2 + offset), *args,
                                      metadata={"beneficial_owner": "ENTITY_X", "execution_venue": "NYSE"}))
        self._cursor += timedelta(seconds=10)
        return sorted(events, key=lambda event: event["timestamp"])

    def generate_spoofing(self) -> List[Dict]:
        instrument = self.random.choice(self.INSTRUMENTS)
        trader = self.random.choice(self.TRADERS)
        account, firm = "SPOOF_ACC", "FIRM_B"
        price = self.PRICES[instrument]
        spoof_id = self._next_order_id()
        quantity = self.random.randint(500, 1000) * 100
        # A large sell quote rests above the bid; it need not cross and execute.
        args = (instrument, trader, account, firm, "SELL", quantity, price * 1.001, spoof_id)
        events = [self._event("ORDER", self._cursor, *args,
                             metadata={"order_type": "LIMIT", "beneficial_owner": "ENTITY_S", "away_from_market": True})]
        for index in range(3):
            small_id = self._next_order_id()
            small_args = (instrument, trader, account, firm, "BUY", self.random.randint(1, 3) * 100,
                          price - index * 0.01, small_id)
            events.append(self._event("ORDER", self._cursor + timedelta(seconds=1 + index), *small_args,
                                      metadata={"order_type": "LIMIT", "beneficial_owner": "ENTITY_S"}))
            events.append(self._event("EXECUTION", self._cursor + timedelta(seconds=1.5 + index), *small_args,
                                      metadata={"execution_venue": "NASDAQ", "beneficial_owner": "ENTITY_S"}))
        events.append(self._event("CANCEL", self._cursor + timedelta(seconds=4), *args,
                                  parent_order_id=spoof_id,
                                  metadata={"cancel_reason": "trader_cancel", "beneficial_owner": "ENTITY_S"}))
        self._cursor += timedelta(seconds=10)
        return sorted(events, key=lambda event: event["timestamp"])

    def generate_scenario(self, scenario_name: str) -> List[Dict]:
        if scenario_name == "wash_trade":
            return self.generate_wash_trade()
        if scenario_name == "spoofing":
            return self.generate_spoofing()
        if scenario_name == "normal":
            return self.generate_normal_trading(50)
        if scenario_name == "mixed":
            events = self.generate_normal_trading(20)
            events.extend(self.generate_wash_trade())
            events.extend(self.generate_normal_trading(10))
            events.extend(self.generate_spoofing())
            events.extend(self.generate_normal_trading(20))
            return sorted(events, key=lambda event: event["timestamp"])
        raise ValueError(f"Unknown replay scenario: {scenario_name}")
