#!/usr/bin/env python

from distutils.core import setup

setup(name='polos',
      version='0.1',
      description='NTP-based local time synchronization tool',
      author='Thomas Vincent',
      author_email='thomas.tv.vincent@gmail.com',
      url='https://github.com/thomas-vincent/polos',
      package_dir={'': 'python'},
      packages=find_packages('python'),
      licence='GPL3',
      classifiers=[
          "Development Status :: 3 - Alpha",
          "Environment :: Console",
          "Environment :: Win32 (MS Windows)",
          "Intended Audience :: Information Technology",
          "Intended Audience :: System Administrators",
          "Intended Audience :: Science/Research",
          "Programming Language :: Python :: 3",
          "Operating System :: POSIX :: Linux",
          "Operating System :: Microsoft :: Windows :: Windows 8.1",
          "Topic :: System :: Networking :: Time Synchronization",
          "Topic :: System :: Monitoring",
          "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
          "Natural Language :: English"],
)
