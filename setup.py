from setuptools import setup, find_packages


def get_requirements():
    # intentionally naive, does not support include files etc
    with open("./requirements.txt") as fp:
        return fp.read().split()


setup(
    name="leport",
    packages=find_packages(exclude=["tests", "tests.*"]),
    version="0.1.0",
    description="ports-like package management",
    author="Jesper Wendel Devantier",
    url="https://github.com/jwdevantier/leport",
    license="MIT",
    install_requires=get_requirements(),
    options={"bdist_wheel": {"universal": True}},
    entry_points = {
        "console_scripts": [
            "leport=leport.main:main"
        ]
    },
    classifiers=[
        "Programming Language :: Python",
    ]
)
