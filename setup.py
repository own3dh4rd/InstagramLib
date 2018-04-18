from setuptools import setup, find_packages


setup(
    name='InstagramLib',
    version='2018.4.2',
    author='OlegYurchik',
    packages=find_packages(),
    url='https://github.com/OlegYurchik/InstagramLib',
    classifiers=(
        'Environment :: Console',
        'Operating System :: POSIX',
        'Operating System :: Unix',
        'Programming Language :: Python :: 3.6',
    ),
    install_requires=['requests'],
    extras_require={
        'dev': [],
    },
)
