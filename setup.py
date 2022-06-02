from setuptools import setup

setup(
    name="algolia",
    version="0.1.0",
    packages=["algolia"],
    package_dir={"algolia": "algolia"},
    description="Algolia API client",
    install_requires=["algoliasearch", "gql==2.0.0"],
    entry_points={
        "saleor.plugins": ["algolia = algolia.plugin:AlgoliaPlugin"],
    },
)
