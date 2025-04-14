#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Copyright (C) 2025 Markus Stockhausen <markus.stockhausen@gmx.de>
#
# The SerDes of Realtek switches need well defined startup patching. The
# GPL source drops contain several magic sequences that cannot be explained
# in detail. Writing that magic data into some structure into a header and
# reference that inside the code would require to include all sequences
# for all 4 platforms (RTL838x, RTL839x, RTL930x and RTL931x) into the
# kernel and a lot of references and individual coding.
#
# Define a generic firmware data format that can be consumed by the driver.
# This script takes a standalone C header file (not used in the normal kernel
# sources) and generates a binary firmware that can be loaded inside the 
# phy-rtk-otto-serdes driver.
#
# A firmware file contains a head, a directory and at the end the raw patch 
# data. See structure rtsds_fw_head, rtsds_fw_dir an rtsds_fw_seq in the
# driver for more details.
#
# header
#	(u32) magic = 0x83009300, see RTSDS_FW_MAGIC
#	(u32) CRC checksum of the following data
#	(u32) filesize
#	(u32) directory size = number of sequences
#
# directory with one or more blocks consisting of
#	(u32) id of the sequence. See RTSDS_FW_EVT_xxx
#	(u32) offset in bytes of patch data for this directory entry
#	(u32) length in bytes of patch data for this directory entry
#	(u32) future use to avoid structure breakage
#
# patch data with one ore more blocks consisting of
#	(u16) action to process. See RTSDS_FW_OP_xxx
#	(u16) mode for which the command is to be executed. See RTSDS_FW_MODE_xxx
#	(u16) SerDes ports bitmask for which the command is to be executed
#	(u16) page for action
#	(u16) register for action
#	(u16) value for action
#	(u16) mask for write operations
#	(u16) future use to avoid structure breakage

import argparse
import binascii
import re
import os

HEADER = 0x83009300
DATASIZE = 2

# remove_comments() removes /* */ or // c style comments and is quotation
# mark aware.

def remove_comments(string):
    pattern = r"(\".*?\"|\'.*?\')|(/\*.*?\*/|//[^\r\n]*$)"
    regex = re.compile(pattern, re.MULTILINE|re.DOTALL)
    def _replacer(match):
        if match.group(2) is not None:
            return ""
        else:
            return match.group(1)
    return regex.sub(_replacer, string)

# read_header_file() reads the C header input. Sanitizing might need
# some improvement but it works quite well. The function returns a list
# of string structures that resemble the input file converted into integers

def read_header_file(indata):
    indata = remove_comments(indata)
    # Remove C declarations (brackets, semicolon, ...) and multiple whitespaces
    indata = re.sub('[\\(\\)\\{\\}\\,\\;]', ' ', indata)
    indata = ' '.join(indata.split())

    # Filter file content into multiple sequences
    seq = indata.split('EVT_')
    seq = ["EVT_" + d for d in seq if "=" in d]    

    # Filter file content into multiple defines
    defs = indata.split('#define')
    defs = [d for d in defs if "=" not in d and d]

    # replace defines in sequences
    for d in defs:
      d = ' '.join(d.split())
      kvp = d.split(" ")
      name = kvp[0]
      val = kvp[1]
      seq = [s.replace(name, val) for s in seq]
   
    return seq

# convert_intermediate_data() takes the sanitized string input and 
# transforms into integer sequences. 

def convert_intermediate_data(indata):
    blocks = []
    
    # Translate the sequence strings into binary block data
    for d in indata:
        kvp = d.split("=")
        name = kvp[0].strip().upper()
        vals = kvp[1].split()
        # first element of block is the sequence id
        block = [int(name)]
        # second element of block is the patch data
        block.append(list(map(lambda x:int(x,0) % (1<<16),vals)))
        blocks.append(block)
    
    return blocks

# create_raw_data() takes the intermediate data and produces the binary
# output data. As the Realtek chips are MIPS big endian the output data
# is generated directly in the target endian order.

def create_raw_data(blocks):
    # Create directory size
    dirdata = bytearray(len(blocks).to_bytes(4, byteorder = 'big'))
    seqpos = 16 + 16 * len(blocks)

    # Create directory entries with sequence id and offset
    for b in blocks:
        seqlen = len(b[1]) * DATASIZE
        # event id of this directory entry
        dirdata += bytearray(b[0].to_bytes(4, byteorder = 'big'))
        # offset in bytes of the sequence of this directory entry
        dirdata += bytearray(seqpos.to_bytes(4, byteorder = 'big'))
        # length in bytes of the sequence of this directory entry
        dirdata += bytearray(seqlen.to_bytes(4, byteorder = 'big'))
        # dummy data for later use
        dirdata += bytearray(b'\x00\x00\x00\x00')
        seqpos += seqlen
        
    # Create patch data
    patchdata = bytearray()
    for b in blocks:
        for v in b[1]:
            patchdata += bytearray(v.to_bytes(DATASIZE, byteorder = 'big'))

    # add filesize before directory
    filesize = 12 + len(dirdata) + len(patchdata)
    dirdata = filesize.to_bytes(4, byteorder = 'big') + dirdata

    # Create header consisting of identifier and CRC sum
    crc = binascii.crc32(dirdata + patchdata) % (1<<32)
    hdrdata = bytearray(HEADER.to_bytes(4, byteorder = 'big'))
    hdrdata += bytearray(crc.to_bytes(4, byteorder = 'big'))
    return hdrdata + dirdata + patchdata

# show_result() gives helpful output of what to do with the file.

def show_result(filename):
    print("Created Realtek RTL83xx/RTL93xx firmware file " + filename)
    print("Example for DTS inclusion")
    print("")
    print("  serdes: phy@1b0003b0 {")
    print("    compatible = \"realtek,rtl9302b-serdes\";")
    print("    reg = <0x1b0003b0 0x8>;")
    print("    firmware-name = \"" + os.path.basename(filename) + "\"")
    print("    #phy-cells = <4>;")
    print("  };")

parser = argparse.ArgumentParser(description='Create Realtek firmware.')
parser.add_argument('headerfile', type=argparse.FileType('r'))
parser.add_argument('firmwarefile', type=argparse.FileType('wb'))
args = parser.parse_args()

header = read_header_file(args.headerfile.read())
intermediate = convert_intermediate_data(header)
firmware = create_raw_data(intermediate)
args.firmwarefile.write(firmware)
show_result(args.firmwarefile.name)
