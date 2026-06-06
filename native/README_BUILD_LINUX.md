# Linux build

From the `native` folder:

```bash
./build_linux.sh
```

If you use `pyenv`, set the local Python version first:

```bash
pyenv local 3.11
./build_linux.sh
```

Or explicitly pass a Python interpreter:

```bash
PYTHON_BIN="$(pyenv which python)" ./build_linux.sh
```

The script builds:

```text
native/build/hippo_occ_core.cpython-311-x86_64-linux-gnu.so
```

and copies it to:

```text
native/linux-x64/hippo_occ_core.so
```
