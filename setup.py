from setuptools import setup

setup(
    name='motorparts',
    version='1.0.0',
    description='Python 3 API for motorparts.com.',
    url='https://github.com/happyleavesaoc/python-motorparts/',
    license='MIT',
    author='happyleaves',
    author_email='happyleaves.tfr@gmail.com',
    packages=['motorparts'],
    install_requires=['beautifulsoup4==4.5.1', 'requests==2.12.4'],
    classifiers=[
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3',
    ]
)
