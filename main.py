from prefect import flow, task

@task(log_prints=True)
def text():
    print("WHAT UP")

@task(log_prints=True)
def adding():
    print(10+10)
    return 100

@flow(log_prints=True)
def my_flow():
    text()
    adding()
    print("Hello, world!")


if __name__ == "__main__":
    my_flow()