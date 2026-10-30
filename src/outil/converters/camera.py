import struct


def convert_camera(data):
    def read64(offset):
        if not 0 <= offset <= len(data) - 8:
            raise ValueError("Camera pointer outside the file")
        return struct.unpack_from("<Q", data, offset)[0]

    directory = read64(0)
    if directory + 5 * 16 > len(data):
        raise ValueError("Truncated camera table")
    groups = []
    for group in range(5):
        count = read64(directory + group * 16)
        pointers = read64(directory + group * 16 + 8)
        if count > 4096 or pointers + count * 8 > len(data):
            raise ValueError("Invalid camera list")
        scripts = []
        for index in range(count):
            start = read64(pointers + index * 8)
            offset = start
            while True:
                if offset + 4 > len(data):
                    raise ValueError("Truncated camera script")
                operation, words = struct.unpack_from("<HH", data, offset)
                if not 0 < words < 64 or offset + words * 4 > len(data):
                    raise ValueError("Invalid camera command")
                offset += words * 4
                if operation in (12, 38):
                    scripts.append(data[start:offset])
                    break
        groups.append(scripts)

    output = bytearray(0x40)
    offsets = []
    for group in groups:
        offsets.append([])
        for script in group:
            offsets[-1].append(len(output))
            output.extend(script)
    arrays = []
    for group in offsets:
        arrays.append(len(output))
        for offset in group:
            output.extend(struct.pack("<I", offset))
    output.extend(bytes(-len(output) % 16))
    directory = len(output)
    for group, pointer in zip(offsets, arrays):
        output.extend(struct.pack("<II", len(group), pointer))
    output.extend(bytes(-len(output) % 16))
    struct.pack_into("<II", output, 0, directory, 0x20)
    return bytes(output)
