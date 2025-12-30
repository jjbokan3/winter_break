from prefect import flow, task

@task(log_prints=True)
def text():
    print("WHAT UP")

@flow(log_prints=True)
def my_flow():
    text()
    print("Hello, world!")


if __name__ == "__main__":
    my_flow()