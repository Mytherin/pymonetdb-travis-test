# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0.  If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
#
# Copyright 1997 - July 2008 CWI, August 2008 - 2016 MonetDB B.V.

"""
This is the python implementation of the mapi protocol.
"""

import socket
import logging
import struct
import hashlib
import os
import sys
from six import BytesIO, PY3

try:
    import snappy
    # there is a different library called "snappy" that is NOT the compression library
    # hence even on successful import we test if this "snappy" is the correct one
    if "foo" != snappy.decompress(snappy.compress("foo")):
        raise Exception("Snappy is not capable of compressing data!")
    HAVE_SNAPPY = True
except:
    HAVE_SNAPPY = False

from pymonetdb.exceptions import OperationalError, DatabaseError,\
    ProgrammingError, NotSupportedError, IntegrityError

class Protocol:
    prot9 = 1
    prot10 = 2

class Compression:
    none = 1
    snappy = 2
    lz4 = 3

class Endianness:
    little = 1
    big = 2

def get_byte_order():
    import sys
    return Endianness.little if sys.byteorder == 'little' else Endianness.big

logger = logging.getLogger(__name__)

MAX_PACKAGE_LENGTH = (1024 * 8) - 2

MSG_PROMPT = b""
MSG_MORE = b"\1\2\n"
MSG_INFO = b"#"
MSG_ERROR = b"!"
MSG_Q = b"&"
MSG_QTABLE = b"&1"
MSG_QUPDATE = b"&2"
MSG_QSCHEMA = b"&3"
MSG_QTRANS = b"&4"
MSG_QPREPARE = b"&5"
MSG_QBLOCK = b"&6"
MSG_HEADER = b"%"
MSG_NEW_RESULT_HEADER = b"*"
MSG_INITIAL_RESULT_CHUNK = b"+"
MSG_RESULT_CHUNK = b"-"
MSG_TUPLE = b"["
MSG_TUPLE_NOSLICE = b"="
MSG_REDIRECT = b"^"
MSG_OK = b"=OK"

STATE_INIT = 0
STATE_READY = 1


# MonetDB error codes
errors = {
    '42S02!': OperationalError,  # no such table
    'M0M29!': IntegrityError,    # INSERT INTO: UNIQUE constraint violated
    '2D000!': IntegrityError,    # COMMIT: failed
    '40000!': IntegrityError,    # DROP TABLE: FOREIGN KEY constraint violated
}


def handle_error(error):
    """Return exception matching error code.

    args:
        error (str): error string, potentially containing mapi error code

    returns:
        tuple (Exception, formatted error): returns OperationalError if unknown
            error or no error code in string

    """

    if len(error) > 6 and error[:6] in errors:
        return errors[error[:6]], error[6:]
    else:
        return OperationalError, error


# noinspection PyExceptionInherit
class Connection(object):
    """
    MAPI (low level MonetDB API) connection
    """
    def __init__(self):
        self.state = STATE_INIT
        self._result = None
        self.socket = ""
        self.hostname = ""
        self.port = 0
        self.username = ""
        self.password = ""
        self.database = ""
        self.language = ""
        self.protocol = Protocol.prot9
        self.compression = Compression.none
        self.endianness = get_byte_order()
        self.blocksize = -1

    def connect(self, database, username, password, language, hostname=None,
                port=None, unix_socket=None):
        """ setup connection to MAPI server

        unix_socket is used if hostname is not defined.
        """

        if hostname and hostname[:1] == '/' and not unix_socket:
            unix_socket = '%s/.s.monetdb.%d' % (hostname, port)
            hostname = None
        if not unix_socket and os.path.exists("/tmp/.s.monetdb.%i" % port):
            unix_socket = "/tmp/.s.monetdb.%i" % port
        elif not hostname:
            hostname = 'localhost'

        self.hostname = hostname
        self.port = port
        self.username = username
        self.password = password
        self.database = database
        self.language = language
        self.unix_socket = unix_socket

        if hostname:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # For performance, mirror MonetDB/src/common/stream.c socket settings.
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 0)
            self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.socket.connect((hostname, port))
        else:
            self.socket = socket.socket(socket.AF_UNIX)
            self.socket.connect(unix_socket)
            if self.language != 'control':
                # don't know why, but we need to do this
                self.socket.send(encode('0'))

        if not (self.language == 'control' and not self.hostname):
            # control doesn't require authentication over socket
            self._login()

        self.state = STATE_READY

    def _login(self, iteration=0):
        """ Reads challenge from line, generate response and check if
        everything is okay """

        challenge = self._getblock().decode('utf-8')
        self.blocksize = 1000000
        (response, protocol, compression) = self._challenge_response(challenge, self.blocksize)
        self._putblock(response.encode('utf-8'))
        self.protocol = protocol
        self.compression = compression
        prompt = self._getblock().strip()

        if len(prompt) == 0:
            # Empty response, server is happy
            pass
        elif prompt == MSG_OK:
            pass
        elif prompt.startswith(MSG_INFO):
            logger.info("%s" % prompt[1:])

        elif prompt.startswith(MSG_ERROR):
            logger.error(prompt[1:])
            raise DatabaseError(prompt[1:])

        elif prompt.startswith(MSG_REDIRECT):
            # a redirect can contain multiple redirects, for now we only use
            # the first
            redirect = prompt.split()[0][1:].split(':')
            if redirect[1] == "merovingian":
                logger.debug("restarting authentication")
                if iteration <= 10:
                    self._login(iteration=iteration + 1)
                else:
                    raise OperationalError("maximal number of redirects "
                                           "reached (10)")

            elif redirect[1] == "monetdb":
                self.hostname = redirect[2][2:]
                self.port, self.database = redirect[3].split('/')
                self.port = int(self.port)
                logger.info("redirect to monetdb://%s:%s/%s" %
                            (self.hostname, self.port, self.database))
                self.socket.close()
                self.connect(hostname=self.hostname, port=self.port,
                             username=self.username, password=self.password,
                             database=self.database, language=self.language)

            else:
                raise ProgrammingError("unknown redirect: %s" % prompt)

        else:
            raise ProgrammingError("unknown state: %s" % prompt)

    def disconnect(self):
        """ disconnect from the monetdb server """
        self.state = STATE_INIT
        self.socket.close()

    def read_response(self):
        response = self._getblock()
        if not len(response):
            return ""
        if response.startswith(MSG_OK):
            return response[3:].strip() or ""
        if response == MSG_MORE:
            # tell server it isn't going to get more
            return self.cmd("")

        # If we are performing an update test for errors such as a failed
        # transaction.

        # We are splitting the response into lines and checking each one if it
        # starts with MSG_ERROR. If this is the case, find which line records
        # the error and use it to call handle_error.
        if response[:2] == MSG_QUPDATE:
            lines = response.split(b'\n')
            if any([l.startswith(MSG_ERROR) for l in lines]):
                index = next(i for i, v in enumerate(lines) if v.startswith(MSG_ERROR))
                exception, string = handle_error(lines[index][1:])
                raise exception(string)

        if response[0:1] in [MSG_Q, MSG_HEADER, MSG_TUPLE, MSG_NEW_RESULT_HEADER, MSG_INITIAL_RESULT_CHUNK, MSG_RESULT_CHUNK]:
            return response
        elif response[0:1] == MSG_ERROR:
            exception, string = handle_error(response[1:])
            raise exception(string)
        elif response[0:1] == MSG_INFO:
            logger.info("%s" % (response[1:]))
        elif self.language == 'control' and not self.hostname:
            if response.startswith("OK"):
                return response[2:].strip() or ""
            else:
                return response
        else:
            raise ProgrammingError("unknown state: %s" % response)


    def cmd(self, operation):
        """ put a mapi command on the line"""
        logger.debug("executing command %s" % operation)

        if self.state != STATE_READY:
            raise(ProgrammingError, "Not connected")

        self._putblock(operation.encode('utf-8') if PY3 else operation)
        return self.read_response()

    def _challenge_response(self, challenge, blocksize):
        """ generate a response to a mapi login challenge """
        challenges = challenge.split(':')
        salt, identity, protocol, hashes, endian = challenges[:5]
        password = self.password

        if protocol == '9':
            algo = challenges[5]
            try:
                h = hashlib.new(algo)
                h.update(password.encode())
                password = h.hexdigest()
            except ValueError as e:
                raise NotSupportedError(e.message)
        else:
            raise NotSupportedError("We only speak protocol v9")

        h = hashes.split(",")
        if "SHA1" in h:
            s = hashlib.sha1()
            s.update(password.encode())
            s.update(salt.encode())
            pwhash = "{SHA1}" + s.hexdigest()
        elif "MD5" in h:
            m = hashlib.md5()
            m.update(password.encode())
            m.update(salt.encode())
            pwhash = "{MD5}" + m.hexdigest()
        else:
            raise NotSupportedError("Unsupported hash algorithms required"
                                    " for login: %s" % hashes)

        protocol = Protocol.prot9
        compression = Compression.none
        response = ["BIG", self.username, pwhash, self.language, self.database]
        if "PROT10" in h:
            # protocol 10 is supported
            protocol = Protocol.prot10
            _compression = "COMPRESSION_NONE"
            if self.hostname != "localhost" and "COMPRESSION_SNAPPY" in h and HAVE_SNAPPY:
                _compression = "COMPRESSION_SNAPPY"
                compression = Compression.snappy
            response = ["LIT" if  self.endianness == Endianness.little else "BIG", self.username, pwhash, self.language, self.database, "PROT10", _compression, str(blocksize)]
        return (":".join(response) + ":", protocol, compression)

    def _getblock(self):
        """ read one mapi encoded block """
        if (self.language == 'control' and not self.hostname):
            return self._getblock_socket()  # control doesn't do block splitting when using a socket
        else:
            return self._getblock_inet()

    def _getblock_inet(self):
        result = BytesIO()
        last = 0
        while not last:
            if self.protocol == Protocol.prot9:
                flag = self._getbytes(2)
                unpacked = struct.unpack('<H', flag)[0]  # little endian short
                length = unpacked >> 1
                last = unpacked & 1
            else:
                flag = self._getbytes(8)
                unpacked = struct.unpack('<q', flag)[0]  # little endian long long
                length = unpacked >> 1
                last = unpacked & 1
            if length > 0:
                block = self._getbytes(length)
                if self.compression == Compression.snappy:
                    block = snappy.uncompress(block)
                result.write(block)
        return result.getvalue()

    def _getblock_socket(self):
        buffer = BytesIO()
        while True:
            x = self.socket.recv(1)
            if len(x):
                buffer.write(x)
            else:
                break
        return buffer.getvalue().strip()

    def _getbytes(self, bytes_):
        """Read an amount of bytes from the socket"""
        result = BytesIO()
        count = bytes_
        while count > 0:
            recv = self.socket.recv(count)
            if len(recv) == 0:
                raise OperationalError("Server closed connection")
            count -= len(recv)
            result.write(recv)
        return result.getvalue()

    def _putblock(self, block):
        """ wrap the line in mapi format and put it into the socket """
        if (self.language == 'control' and not self.hostname):
            return self.socket.send(encode(block))  # control doesn't do block splitting when using a socket
        else:
            self._putblock_inet(block)

    def _putblock_inet(self, block):
        pos = 0
        last = 0
        while not last:
            data = block[pos:pos + MAX_PACKAGE_LENGTH]
            if self.compression == Compression.snappy:
                data = snappy.compress(data)
            length = len(data)
            if length < MAX_PACKAGE_LENGTH:
                last = 1
            if self.protocol == Protocol.prot9:
                flag = struct.pack('<H', (length << 1) + last)
            else:
                flag = struct.pack('<q', (length << 1) + last) # little endian
            self.socket.send(flag)
            self.socket.send(data)
            pos += length

    def __del__(self):
        if self.socket:
            self.socket.close()
