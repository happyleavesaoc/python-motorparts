from setuptools import setup

setup(
    name='motorparts',
    version='1.1.0',
    description='Python 3 API for mopar.com.',
    url='https://github.com/happyleavesaoc/python-motorparts/',
    license='MIT',
    author='happyleaves',
    author_email='happyleaves.tfr@gmail.com',
    packages=['motorparts'],
    install_requires=['beautifulsoup4==4.5.1', 'requests>=2.20.0'],
    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3',
    ]
)
