#!/usr/bin/env python
from setuptools import setup, find_packages

test_deps = [
    'coverage',
    'pytest-cov',
    'pytest-timeout',
    'moto',
    'mock',
    'pytest',
]

extras = {
    'testing': test_deps,
}

setup(
    name='mozetl',
    version='0.1',
    description='Python ETL jobs for Firefox Telemetry to be scheduled on Airflow.',
    author='Ryan Harter',
    author_email='harterrt@mozilla.com',
    url='https://github.com/mozilla/python_mozetl.git',
    packages=find_packages(exclude=['tests']),
    include_package_data=True,
    install_requires=[
        'arrow==0.10.0',
        'click==6.7',
        'click_datetime==0.2',
        'numpy==1.13.3',
        'pyspark==2.2.0.post0',
        'python_moztelemetry==0.8.9',
        'requests==2.18.4',
        'scipy==1.0.0rc1',
    ],
    tests_require=test_deps,
    extras_require=extras,
)
