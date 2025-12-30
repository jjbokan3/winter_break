from prefect import flow, task, get_run_logger

@task()
def text():
    print("WHAT UP")

@task()
def adding():
    print(10+10)
    return 100

@flow()
def my_flow():
    logger = get_run_logger()
    logger.info("Hello, world!")
    logger.error("Yikes")
    logger.debug("Debug message")
    logger.warning("Warning!")
    logger.info("Hello again!")
    text()
    adding()
    print("Hello, world!")


if __name__ == "__main__":
    my_flow()