"""Run after installing Gauge. No SDK, credentials, or network required."""

import asyncio

from gauge import Case, Experiment, ModelOutput


class DemoTarget:
    def __init__(self, name: str) -> None:
        self.name = name

    def describe(self):
        return {"adapter": "offline-demo", "model": self.name}

    async def invoke(self, case: Case, *, instructions: str) -> ModelOutput:
        label = "household" if case.input == "Dish soap" else "grocery"
        if self.name == "candidate" and case.input == "Paper plates":
            label = "household"
        return ModelOutput(text=label)


async def main() -> None:
    report = await Experiment(
        instructions="Classify as grocery, household, or other.",
        reference=DemoTarget("reference"),
        candidates=[DemoTarget("candidate")],
    ).run([Case("soap", "Dish soap"), Case("plates", "Paper plates")])
    print(report.to_json())
    print(report.summary())
    print("The reference deliberately mislabels paper plates. Agreement is not accuracy.")


if __name__ == "__main__":
    asyncio.run(main())
