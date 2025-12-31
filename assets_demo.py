from pathlib import Path

from prefect import flow
from prefect.assets import materialize


@materialize("file://./prefect-assets-demo/raw.txt")
def make_raw() -> str:
    path = Path("prefect-assets-demo/raw.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("hello\n", encoding="utf-8")
    return str(path)


@materialize("file://./prefect-assets-demo/processed.txt")
def make_processed(raw_path: str) -> str:
    raw = Path(raw_path).read_text(encoding="utf-8")
    out_path = Path("prefect-assets-demo/processed.txt")
    out_path.write_text(raw.upper(), encoding="utf-8")
    return str(out_path)


@flow
def assets_demo_flow():
    raw_path = make_raw()
    processed_path = make_processed(raw_path)
    return {"raw": raw_path, "processed": processed_path}


if __name__ == "__main__":
    assets_demo_flow()