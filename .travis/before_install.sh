#!/bin/bash -ve

# install monetdb
sudo apt-get install -qy software-properties-common libpcre3-dev libxml2-dev autoconf libssl-dev gettext bison

wget https://dev.monetdb.org/hg/MonetDB/archive/default.tar.gz
tar xvf default.tar.gz
cd MonetDB-default
./bootstrap
./configure --enable-debug --enable-assert --disable-optimize --prefix=$HOME/monetdb-install
make -j
make install
cd ..

export PATH=$HOME/monetdb-install/bin:$PATH

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
monetdbd create $HOME/dbfarm
monetdbd set control=yes $HOME/dbfarm
monetdbd set passphrase=testdb $HOME/dbfarm
monetdbd start $HOME/dbfarm
monetdb create demo
monetdb release demo

# install python test requirements
pip install -r test/requirements.txt
