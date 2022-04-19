from setuptools import setup


def get_requirements():
    # intentionally naive, does not support include files etc
    with open("./requirements.txt") as fp:
        return fp.read().split()


setup(
    name="leport",
    packages=["leport"],
    version="0.1.0",
    description="ports-like package management",
    author="Jesper Wendel Devantier",
    url="https://github.com/jwdevantier/leport",
    license="MIT",
    install_requires=get_requirements(),
    options={"bdist_wheel": {"universal": True}},
    entry_points = {
        "console_scripts": [
            "leport=leport.__main__:main"
        ]
    },
    classifiers=[
        "Programming Language :: Python",
    ]
)
