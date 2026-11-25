# How an NX part file stores its geometry

Notes on the layers `nx2parasolid` reads, as observed in files saved by NX 2206-2606. Byte
layouts are given as found; names of Parasolid node types and fields come from Parasolid's
schema files and the public *Parasolid XT Format Reference*.

## 1. The container (`SPLMSSTR`)

A `.prt` is an archive of named streams. It starts with `SPLMSSTR`; the stream directory is a run
of entries, the first one 4 bytes before the first occurrence of `/Root/`:

| Field | Type |
|---|---|
| name length | `u32` little-endian |
| name | bytes, starts `/Root/` |
| offset | `u64` LE, from the start of the file |
| length | `u64` LE |

Directory-only entries (such as `/Root/` itself) may carry sizes that do not describe stream data
and are skipped. Streams used here:

| Stream | Contents |
|---|---|
| `/Root/UG_PART/UG_PART` | the model, including the Parasolid data (section 2) |
| `/Root/FastLoad/JT` | lightweight display data in JT format (section 5) |
| `/Root/FastLoad/Structure` | assembly structure (section 6) |
| `/Root/images/preview` | JPEG thumbnail |

`container.py`

## 2. Parasolid transmits in `UG_PART`

Body geometry is stored as complete Parasolid *neutral binary* transmits, each compressed with
zlib (`78 01`, `78 5E`, `78 9C` or `78 DA`). The first one is usually preceded by Parasolid's
plain-text header:

```text
**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz**************************
**PARASOLID !"#$%&'()*+,-./:;<=>?@[\]^_`{|}~0123456789**************************
**PART1; ... APPL=NX; ...
**PART2;
SCH=SCH_3801167_38101;
...
**END_OF_HEADER*****************************************************************
```

Each decompressed transmit begins `PS 00 00 00` and its title says what it is:

| Title contains | Kind | Written as | Use |
|---|---|---|---|
| `TRANSMIT FILE created by modeller version N` | part | `.x_b` | a normal Parasolid part file |
| `TRANSMIT FILE (partition) ...` | partition | `.p_b` | the whole partition: WORLD + all bodies |
| `TRANSMIT FILE (deltas) ...` | partition deltas | `.d_b` | undo history, not needed |

Text header + decompressed transmit = a file any Parasolid reader loads. If data after the
header is not zlib, `nx2parasolid` stops (`PossiblyEncrypted`) and reads nothing further.

`streams.py`

## 3. The neutral-binary encoding

All integers big-endian.

```text
"PS" 00 00 00
u8   title length, title
u32  schema-key length, schema key      SCH_<modeller>_<schema version>_<base schema>
6    header bytes (copied unchanged)
node*
u16  1                                  terminator
2    trailing bytes (copied unchanged)
```

Each node:

```text
u16  node type
     [layout of the type - first occurrence only, see below]
u32  number of entries                  only for variable-length node types
u16  index                              pointer target; a set high bit means a 31-bit index
                                        stored in two u16 (high word & 0x7FFF, low word)
fields, in layout order
```

Field type characters (from the schema): `p` pointer (an index, 1 = null), `d`/`t` int32,
`u`/`c`/`l` uint8, `n`/`w` uint16, `f` float64, `v` vector (3 float64), `h` homogeneous vector
(transmitted as 3 float64), `b` box (6 float64), `i` interval (2 float64). Arrays have a fixed
element count from the layout; the last field of a variable-length node repeats *entries* times
(raw bytes for type `c`).

### Layouts: base schema + edits

A transmit does not spell out every node layout. Its schema key names a **base schema**
(the last number, e.g. `13006` -> `sch_13006.s_t`), and on the first occurrence of each node type
the file records how that type differs from the base:

| Byte(s) | Meaning |
|---|---|
| `FF` | unchanged: the base schema's transmitted fields |
| `n`, codes..., `Z` | `n` fields, built by edit codes over the base fields: |
| `C` | copy the next base field |
| `D` | drop the next base field |
| `I` / `A` | insert / append a field: `u8` length + name, `u16` pointer class, `u16` element count + 1, and if the class is 0: `u8` flag + `u8` type character |
| | base fields not reached before `Z` are dropped |

A node type the base schema does not know at all is defined inline instead: `u8` field count,
`u8` length + name, `u8` length + description, the fields (as for `I`), `u8` variable flag.

Schema text files list one node type per line,
`<code> <NAME>; <description>; <transmitted> <field count> <variable>`, followed by one line per
field, `<name>; <type>; <transmitted> <pointer class> <element count>`.

`schema.py`, `xt.py`

## 4. From a partition body to a part file

CAD systems do not read partitions, and NX keeps some bodies only there. For every BODY in a
partition, `partition.py`:

1. makes the BODY the root;
2. collects every node reachable from it through pointer fields, breadth-first, without
   following pointers to the WORLD or to other bodies;
3. sets the BODY's `next`, `previous` and `owner` to null;
4. renumbers the nodes 2..N in that order (BODY = 2, like NX's own part transmits) and rewrites
   pointers; pointers to nodes not collected become null;
5. writes the result with the same schema key and the same layout declarations the partition
   used, followed by the partition's trailing bytes.

Every field value other than a pointer is written exactly as decoded. The result is decoded
again and its topology counted (`census.py`); bodies with no faces (points, wires, construction
geometry) are left out.

## 5. What NX displays: the JT stream

`/Root/FastLoad/JT` is a JT file in zlib-compressed segments. Elements are
`i32 length, GUID (16 bytes, little-endian), u8 base type, payload`. Each displayed body has a
*Tri-Strip Set Shape* node, GUID `10dd1077-2ac8-11d1-9b6b-0080c7bb5997`:

| Payload offset | Value |
|---|---|
| 16 | 6 x `f32` transformed bounding box (metres) |
| 40 | 6 x `f32` untransformed bounding box |
| 64 | `f32` surface area (m²) |

A body is taken to be displayed when a shape's area is within 2 % of the body's and its box lies
inside the body's box + 1 mm (the display mesh is coarse); sheets are matched by a tight box.
This separates a part's own bodies from WAVE-linked copies of other parts and construction
bodies.

`display.py`, `selection.py`

## 6. Assembly structure

`/Root/FastLoad/Structure` stores counted string lists:

```text
01, n + 1, then n strings:  04, length (= characters + 2), UTF-8 text, 00
```

Several lists occur: arrangements, reference-set names, the reference set used by each
occurrence (`MODEL` / `None` pairs), and the part files the part uses - the last list before the
`I/Chronological/` section that is not one of the others. That list names each referenced part
once; quantities and placements live in NX's own object data, which is not decoded.

`structure.py`

## Not decoded

* NX object data in `UG_PART` outside the Parasolid transmits: features, expressions, component
  placements, reference-set membership, attributes.
* Partition deltas (undo history).
