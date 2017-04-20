#!/bin/bash -ve

# install monetdb
sudo apt-get install software-properties-common libpcre3-dev libxml2-dev autoconf libssl-dev

wget https://dev.monetdb.org/hg/MonetDB/archive/default.tar.gz
tar xvf default.tar.gz
cd MonetDB-default
./bootstrap
./configure --enable-debug --enable-assert --disable-optimize
make -j
sudo make install


# sudo apt-get install software-properties-common
# sudo apt-get update -q
# sudo sh -c "echo 'deb http://dev.monetdb.org/downloads/deb/ precise monetdb' > /etc/apt/sources.list.d/monetdb.list"
# wget --output-document=- http://dev.monetdb.org/downloads/MonetDB-GPG-KEY | sudo apt-key add -
# sudo apt-get update -q
# sudo apt-get install -qy monetdb5-sql monetdb-client

# # start database
# sudo sh -c "echo 'STARTUP=yes\nDBFARM=/var/lib/monetdb\n' > /etc/default/monetdb5-sql"
# sudo service monetdb5-sql start

# set up test database
sudo monetdb create demo
sudo monetdb release demo
sudo monetdbd set control=yes /var/lib/monetdb
sudo monetdbd set passphrase=testdb /var/lib/monetdb

# install python test requirements
pip install -r test/requirements.txt
