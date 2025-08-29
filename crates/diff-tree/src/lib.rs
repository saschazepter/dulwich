/*
 * Copyright (C) 2010 Google, Inc.
 * Copyright (C) 2024 Jelmer Vernooij <jelmer@jelmer.uk>
 *
 * Dulwich is dual-licensed under the Apache License, Version 2.0 and the GNU
 * General Public License as public by the Free Software Foundation; version 2.0
 * or (at your option) any later version. You can redistribute it and/or
 * modify it under the terms of either of these two licenses.
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * You should have received a copy of the licenses; if not, see
 * <http://www.gnu.org/licenses/> for a copy of the GNU General Public License
 * and <http://www.apache.org/licenses/LICENSE-2.0> for a copy of the Apache
 * License, Version 2.0.
 */

use pyo3::prelude::*;

use pyo3::exceptions::PyTypeError;
use pyo3::types::{PyBytes, PyList, PyTuple};
use pyo3::Python;

use std::cmp::Ordering;

const S_IFMT: u32 = 0o170000;
const S_IFDIR: u32 = 0o040000;

fn add_hash(get: &Bound<PyAny>, set: &Bound<PyAny>, string: &[u8], py: Python) -> PyResult<()> {
    let str_obj = PyBytes::new(py, string);
    let hash_obj = str_obj.hash()?;
    let value = get.call1((hash_obj,))?;
    let n = string.len();
    set.call1((hash_obj, value.extract::<usize>()? + n))?;
    Ok(())
}

#[pyfunction]
fn _count_blocks(py: Python, obj: &Bound<PyAny>) -> PyResult<PyObject> {
    let default_dict_cls = PyModule::import(py, "collections")?.getattr("defaultdict")?;
    let int_cls = PyModule::import(py, "builtins")?.getattr("int")?;

    let counts = default_dict_cls.call1((int_cls,))?;
    let get = counts.getattr("__getitem__")?;
    let set = counts.getattr("__setitem__")?;

    let chunks = obj.call_method0("as_raw_chunks")?;
    if !chunks.is_instance_of::<PyList>() {
        return Err(PyTypeError::new_err(
            "as_raw_chunks() did not return a list",
        ));
    }

    let num_chunks = chunks.extract::<Vec<PyObject>>()?.len();
    let pym = py.import("dulwich.diff_tree")?;
    let block_size = pym.getattr("_BLOCK_SIZE")?.extract::<usize>()?;
    let mut block: Vec<u8> = Vec::with_capacity(block_size);

    for i in 0..num_chunks {
        let chunk = chunks.get_item(i)?;
        if !chunk.is_instance_of::<PyBytes>() {
            return Err(PyTypeError::new_err("chunk is not a string"));
        }
        let chunk_str = chunk.extract::<&[u8]>()?;

        for c in chunk_str {
            block.push(*c);
            if *c == b'\n' || block.len() == block_size {
                add_hash(&get, &set, &block, py)?;
                block.clear();
            }
        }
    }
    if !block.is_empty() {
        add_hash(&get, &set, &block, py)?;
    }

    Ok(counts.into_pyobject(py).unwrap().into())
}

#[pyfunction]
fn _is_tree(_py: Python, entry: &Bound<PyAny>) -> PyResult<bool> {
    if entry.is_none() {
        return Ok(false);
    }

    let mode = entry.getattr("mode")?;

    if mode.is_none() {
        Ok(false)
    } else {
        let lmode = mode.extract::<u32>()?;
        Ok((lmode & S_IFMT) == S_IFDIR)
    }
}

fn tree_entries(path: &[u8], tree: &Bound<PyAny>, py: Python) -> PyResult<Vec<PyObject>> {
    if tree.is_none() {
        return Ok(Vec::new());
    }

    let dom = py.import("dulwich.objects")?;
    let tree_entry_cls = dom.getattr("TreeEntry")?;

    let items = tree
        .call_method1("iteritems", (true,))?
        .extract::<Vec<PyObject>>()?;

    let mut result = Vec::new();
    for item in items {
        let (name, mode, sha) = item.extract::<(Vec<u8>, u32, PyObject)>(py)?;

        let mut new_path = Vec::with_capacity(path.len() + name.len() + 1);
        if !path.is_empty() {
            new_path.extend_from_slice(path);
            new_path.push(b'/');
        }
        new_path.extend_from_slice(name.as_slice());

        let tree_entry = tree_entry_cls.call1((PyBytes::new(py, &new_path), mode, sha))?;
        result.push(tree_entry.into_pyobject(py).unwrap().into());
    }

    Ok(result)
}

fn entry_path_cmp(entry1: &Bound<PyAny>, entry2: &Bound<PyAny>) -> PyResult<Ordering> {
    let path1_o = entry1.getattr("path")?;
    let path1 = path1_o.extract::<&[u8]>()?;
    let path2_o = entry2.getattr("path")?;
    let path2 = path2_o.extract::<&[u8]>()?;
    Ok(path1.cmp(path2))
}

#[pyfunction]
fn _merge_entries(
    py: Python,
    path: &[u8],
    tree1: &Bound<PyAny>,
    tree2: &Bound<PyAny>,
) -> PyResult<PyObject> {
    let entries1 = tree_entries(path, tree1, py)?;
    let entries2 = tree_entries(path, tree2, py)?;

    let mut result = Vec::new();

    let mut i1 = 0;
    let mut i2 = 0;
    while i1 < entries1.len() && i2 < entries2.len() {
        let cmp = entry_path_cmp(entries1[i1].bind(py), entries2[i2].bind(py))?;
        let (e1, e2) = match cmp {
            Ordering::Equal => (entries1[i1].clone_ref(py), entries2[i2].clone_ref(py)),
            Ordering::Less => (entries1[i1].clone_ref(py), py.None()),
            Ordering::Greater => (py.None(), entries2[i2].clone_ref(py)),
        };
        let pair = PyTuple::new(py, &[e1, e2]).unwrap();
        result.push(pair);
        match cmp {
            Ordering::Equal => {
                i1 += 1;
                i2 += 1;
            }
            Ordering::Less => {
                i1 += 1;
            }
            Ordering::Greater => {
                i2 += 1;
            }
        }
    }

    while i1 < entries1.len() {
        let pair = PyTuple::new(py, &[entries1[i1].clone_ref(py), py.None()]).unwrap();
        result.push(pair);
        i1 += 1;
    }

    while i2 < entries2.len() {
        let pair = PyTuple::new(py, &[py.None(), entries2[i2].clone_ref(py)]).unwrap();
        result.push(pair);
        i2 += 1;
    }

    Ok(PyList::new(py, &result).unwrap().unbind().into())
}

#[pymodule]
fn _diff_tree(_py: Python, m: &Bound<PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(_count_blocks, m)?)?;
    m.add_function(wrap_pyfunction!(_is_tree, m)?)?;
    m.add_function(wrap_pyfunction!(_merge_entries, m)?)?;
    Ok(())
}
