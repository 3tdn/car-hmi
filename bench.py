import time
import asyncio
from dataclasses import dataclass

from src.can_simulator.simulator import CANSimulator, Scenario, ScenarioStep
import can

class DummyDB:
    def __init__(self):
        self.messages = {}

    def encode_message(self, msg_id, signals):
        return can.Message(arbitration_id=msg_id, data=b'\x00'*8)

@dataclass
class DummyMsg:
    msg_id: int
    signals: dict

@dataclass
class DummySig:
    minimum: float = 0.0

async def main():
    db = DummyDB()
    # 5000 thông điệp, mỗi thông điệp 10 tín hiệu -> tổng 50.000 tín hiệu
    for i in range(5000):
        signals = {f"Sig_{i}_{j}": DummySig() for j in range(10)}
        db.messages[i] = DummyMsg(msg_id=i, signals=signals)

    # 1000 bước, mỗi bước cập nhật 50 tín hiệu
    steps = []
    for s in range(100):
        signals = {f"Sig_{i}_0": float(s) for i in range(500)}
        steps.append(ScenarioStep(at_sec=0.0, signals=signals))

    scenario = Scenario(name="bench", duration_sec=0.1, steps=steps)
    bus = can.Bus(interface="virtual", channel="bench_bus")

    sim = CANSimulator(bus=bus, db=db, scenario=scenario, loop=False)

    start = time.perf_counter()
    # Tất cả bước sẽ được xử lý ngay vì at_sec = 0.0
    await sim.start()
    end = time.perf_counter()

    bus.shutdown()

    print(f"Time taken: {end - start:.4f} seconds")

if __name__ == "__main__":
    asyncio.run(main())
