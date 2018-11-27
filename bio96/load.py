#!/usr/bin/env python3

import toml
import re, itertools
import pandas as pd
from pathlib import Path
from nonstdlib import plural, indices_from_str
from copy import deepcopy
from .util import *

# Data Structures
# ===============
# `config`
#   A direct reflection of the TOML input file.  Arbitrary parameters can be 
#   specified on a per-experiment, per-plate, per-row, per-column, or per-well 
#   basis.  
#
# `wells`
#   Dictionary where the keys are (row, col) well indices and the values are 
#   dictionaries containing arbitrary information about said well.  This is 
#   basically a version of the `config` data structure where all messy well 
#   names have been resolved to simple indices, and all the parameters have 
#   been resolved on a per-well basis.
#
# `table`
#   A pandas DataFrame derived from the `wells` data structure.  Each row 
#   represents a well, and each column represents one of the fields in the well 
#   dictionaries.  Columns identifying the plate and well are also added.

def load(toml_path, data_loader=None, merge_cols=None,
        path_guess=None, path_required=False):

    try:
        ## Parse the TOML file:
        config, paths, concats = config_from_toml(toml_path, path_guess)
        labels = table_from_config(config, paths)
        labels = pd.concat([labels, *concats], sort=False)

        if path_required or data_loader:
            if 'path' not in labels or labels['path'].isnull().any():
                raise paths.missing_path_error

        if len(labels) == 0:
            raise ConfigError("No wells defined.")

        ## Load the data associated with each well:
        if data_loader is None:
            if merge_cols is not None:
                raise ValueError("Specified columns to merge, but no function to load data!")
            return labels

        data = pd.DataFrame()

        for path in labels['path'].unique():
           df = data_loader(path)
           df['path'] = path
           data = data.append(df, sort=False)

        ## Merge the labels and the data into a single data frame:
        if merge_cols is None:
            return labels, data

        def check_merge_cols(cols, known_cols, attrs):
            unknown_cols = set(cols) - set(known_cols)
            if unknown_cols:
                quoted_join = lambda it: ', '.join(f"'{x}'" for x in it)
                raise ValueError(f"Cannot merge on {quoted_join(unknown_cols)}.  Allowed {attrs} of the `merge_cols` dict: {quoted_join(known_cols)}.")
            return list(cols)

        left_ok = 'well', 'row', 'col', 'row_i', 'col_i', 'plate'
        left_on = check_merge_cols(merge_cols.keys(), left_ok, 'keys')
        right_on = check_merge_cols(merge_cols.values(), data.columns, 'values')

        return pd.merge(
                labels, data,
                left_on=left_on + ['path'],
                right_on=right_on + ['path'],
        )
    except ConfigError as err:
        err.toml_path = toml_path
        raise

def config_from_toml(toml_path, path_guess=None):
    toml_path = Path(toml_path).resolve()
    config = configdict(toml.load(str(toml_path)))

    def iter_paths_from_meta(key):
        if key not in config.meta:
            yield from []
        else:
            paths = config.meta[key]
            if isinstance(paths, str):
                paths = [paths]
            for path in paths:
                yield resolve_path(toml_path, path)

    # Synthesize any available path information.
    paths = PathManager(
            config.meta.get('path'),
            config.meta.get('paths'),
            toml_path,
            path_guess,
    )

    # Include one or more remote files if any are specified.  
    for path in reversed(list(iter_paths_from_meta('include'))):
        defaults, *_ = config_from_toml(path)
        recursive_merge(config, defaults)

    # Load any files to be concatenated.
    concats = []
    for path in iter_paths_from_meta('concat'):
        df = load(path, path_guess=path_guess)
        concats.append(df)

    # Print out any messages contained in the file.
    if 'alert' in config.meta:
        try: print(f"{toml_path.relative_to(Path.cwd())}:")
        except ValueError: print(f"{toml_path}:")
        print(config.meta['alert'])

    config.pop('meta', None)
    return config, paths, concats

def table_from_config(config, paths):
    config = configdict(config)

    if not config.plates:
        wells = wells_from_config(config)
        # Getting the index can raise errors we might not care about if there 
        # aren't any wells (e.g. it doesn't matter if a path doesn't exist if 
        # it won't be associated with any wells).  Skipping the call is a bit 
        # of a hacky way to avoid these errors, but it works.
        index = paths.get_index_for_only_plate() if wells else {}
        return table_from_wells(wells, index)

    else:
        tables = []
        paths.check_named_plates(config.plates)

        for key, plate_config in config.plates.items():
            # Copy to avoid infinite recursion.
            plate_config = recursive_merge(plate_config.copy(), config)
            wells = wells_from_config(plate_config)

            index = paths.get_index_for_named_plate(key) if wells else {}
            tables += [table_from_wells(wells, index)]

        # Make an effort to keep the columns in a reasonable order.  I don't 
        # know why `pd.concat()` doesn't do this on its own...
        cols = tables[-1].columns
        return pd.concat(tables, sort=False)[cols]

def wells_from_config(config, label=None):
    config = configdict(config)
    wells = {}

    def iter_wells(config):
        for key in config:
            for well in key.split(','):
                yield well, config[key]

    def iter_rows(config):
        def row_range(a, b):
            A, B = i_from_row(a), i_from_row(b)
            yield from [
                    row_from_i(x)
                    for x in range(A, B+1)
            ]

        for key in config:
            for row in indices_from_str(key, cast=str, range=row_range):
                yield row, config[key]

    def iter_cols(config):
        def col_range(a, b):
            A, B = j_from_col(a), j_from_col(b)
            yield from [
                    col_from_j(x)
                    for x in range(A, B+1)
            ]

        for key in config:
            for col in indices_from_str(key, cast=str, range=col_range):
                yield col, config[key]

    ## Create and fill in wells defined by 'well' blocks.
    for well, params in iter_wells(config.wells):
        ij = ij_from_well(well)
        if ij in wells:
            raise ConfigError(f"[well.{well}] defined more than once.")
        wells[ij] = deepcopy(params)

    ## Create new wells implied by any 'block' blocks:
    blocks = {}
    pattern = re.compile('(\d+)x(\d+)')

    for size in config.blocks:
        match = pattern.match(size)
        if not match:
            raise ConfigError(f"Unknown [block] size '{size}', expected 'WxH' (where W and H are both positive integers).")

        width, height = map(int, match.groups())
        if width == 0:
            raise ConfigError(f"[block.{size}] has no width.  No wells defined.")
        if height == 0:
            raise ConfigError(f"[block.{size}] has no height.  No wells defined.")

        for top_left, params in iter_wells(config.blocks[size]):
            for ij in iter_ij_in_block(top_left, width, height):
                wells.setdefault(ij, {})
                blocks.setdefault(ij, [])
                blocks[ij].append(deepcopy(params))
    
    ## Create new wells implied by any 'row' & 'col' blocks.

    def simplify_keys(dim):
        before = config.get(dim, {})
        after = {}

        callbacks = {
                'row': (iter_rows, i_from_row),
                'col': (iter_cols, j_from_col),
                'irow': (iter_rows, i_from_row),
                'icol': (iter_cols, j_from_col),
        }
        iter_keys, index_getter = callbacks[dim]
        
        for b, params in iter_keys(before):
            a = index_getter(b)
            after.setdefault(a, {})
            recursive_merge(after[a], params)

        return after

    def sanity_check(dim1, *dim2s):
        if config.get(dim1) \
                and not wells \
                and not blocks \
                and not any(config.get(x) for x in dim2s):
            raise ConfigError(f"Found {plural(config[dim1]):? [{dim1}] block/s}, but no [{'/'.join(dim2s)}] blocks.  No wells defined.")

    rows = simplify_keys('row')
    cols = simplify_keys('col')
    irows = simplify_keys('irow')
    icols = simplify_keys('icol')

    sanity_check('row', 'col', 'icol')
    sanity_check('col', 'row', 'irow')
    for ij in itertools.product(rows, cols):
        wells.setdefault(ij, {})

    sanity_check('irow', 'col')
    for ii, j in itertools.product(irows, cols):
        ij = interleave(ii, j), j
        wells.setdefault(ij, {})

    sanity_check('icol', 'row')
    for i, jj in itertools.product(rows, icols):
        ij = i, interleave(i, jj)
        wells.setdefault(ij, {})

    ## Fill in any wells created above.
    for ij in wells:
        i, j = ij
        ii = interleave(i, j)
        jj = interleave(j, i)

        # Merge in order of precedence: [block], [row/col], top-level.
        # [well] is already accounted for.
        for block in blocks.get(ij, {}):
            recursive_merge(wells[ij], block)

        recursive_merge(wells[ij], rows.get(i, {}))
        recursive_merge(wells[ij], cols.get(j, {}))
        recursive_merge(wells[ij], irows.get(ii, {}))
        recursive_merge(wells[ij], icols.get(jj, {}))
        recursive_merge(wells[ij], config.user)

    return wells
    
def table_from_wells(wells, index):
    table = []
    user_cols = []
    max_j = max([12] + [j for i,j in wells])
    digits = len(str(max_j + 1))
    
    for i, j in wells:
        row, col = row_col_from_ij(i, j)
        well = well_from_ij(i, j)
        well0 = well0_from_well(well, digits=digits)
        user_cols += [x for x in wells[i,j] if x not in user_cols]

        table += [{
                **wells[i,j],
                **index,
                'well': well,
                'well0': well0,
                'row': row, 'col': col,
                'row_i': i, 'col_j': j,
        }]

    # Make an effort to put the columns in a reasonable order:
    columns = ['well', 'well0', 'row', 'col', 'row_i', 'col_j']
    columns += list(index) + user_cols

    return pd.DataFrame(table, columns=columns)


def recursive_merge(config, defaults, overwrite=False):
    for key, default in defaults.items():
        if isinstance(default, dict):
            if isinstance(config.get(key, {}), dict):
                config.setdefault(key, {})
                recursive_merge(config[key], default, overwrite)
            elif overwrite:
                config[key] = deepcopy(default)
        else:
            if overwrite or key not in config:
                config[key] = default

    # Modified in-place, but also returned for convenience.
    return config

def resolve_path(parent_path, child_path):
    parent_dir = Path(parent_path).parent
    child_path = Path(child_path)

    if child_path.is_absolute():
        return child_path
    else:
        return parent_dir / child_path

class PathManager:

    def __init__(self, path, paths, toml_path, path_guess=None):
        self.path = path
        self.paths = paths
        self.toml_path = Path(toml_path)
        self.path_guess = path_guess
        self.missing_path_error = None

    def __str__(self):
        return str({
            'path': self.path,
            'paths': self.paths,
            'toml_path': self.toml_path,
            'path_guess': self.path_guess,
        })

    def check_overspecified(self):
        if self.path and self.paths:
            raise ConfigError(f"Both `meta.path` and `meta.paths` specified; ambiguous.")

    def check_named_plates(self, names):
        self.check_overspecified()

        if self.path is not None:
            raise ConfigError(f"`meta.path` specified with one or more [plate] blocks ({', '.join(names)}).  Did you mean to use `meta.paths`?")

        if isinstance(self.paths, dict):
            if set(names) != set(self.paths):
                raise ConfigError(f"The keys in `meta.paths` ({', '.join(sorted(self.paths))}) don't match the [plate] blocks ({', '.join(sorted(names))})")
        
    def get_index_for_only_plate(self):
        # If there is only one plate:
        # - Have `paths`: Ambiguous, complain.
        # - Have `path`: Use it, complain if non-existent
        # - Have extension: Guess path from stem, complain if non-existent.
        # - Don't have anything: Don't put path in the index

        def make_index(path):
            path = resolve_path(self.toml_path, path)
            if not path.exists():
                raise ConfigError(f"'{path}' does not exist")
            return {'path': path}

        self.check_overspecified()

        if self.paths is not None:
            raise ConfigError(f"`meta.paths` ({self.paths if isinstance(self.paths, str) else ', '.join(self.paths)}) specified without any [plate] blocks.  Did you mean to use `meta.path`?")

        if self.path is not None:
            return make_index(self.path)

        if self.path_guess:
            return make_index(self.path_guess.format(self.toml_path))

        self.missing_path_error = ConfigError(f"Analysis requires a data file, but none was specified and none could be inferred.  Did you mean to set `meta.path`?")
        return {}

    def get_index_for_named_plate(self, name):
        # If there are multiple plates:
        # - Have `path`: Ambiguous, complain.
        # - `paths` is string: Format with name, complain if non-existent or 
        #   if formatting didn't change the path.
        # - `paths` is dict: Make sure the keys match the plates in the config.  
        #   Look up the path, complain if non-existent.
        # - Don't have `paths`: Put the name in the index without a path.
        
        def make_index(name, path):
            path = resolve_path(self.toml_path, path)
            if not path.exists():
                raise ConfigError(f"'{path}' for plate '{name}' does not exist")
            return {'plate': name, 'path': path}

        if self.paths is None:
            self.missing_path_error = ConfigError(f"Analysis requires a data file for each plate, but none were specified.  Did you mean to set `meta.paths`?")
            return {'plate': name}

        if isinstance(self.paths, str):
            return make_index(name, self.paths.format(name))

        if isinstance(self.paths, dict):
            if name not in self.paths:
                raise ConfigError(f"No data file path specified for plate '{name}'")
            return make_index(name, self.paths[name])

        raise ConfigError(f"Expected `meta.paths` to be dict or str, got {type(self.paths)}: {self.paths}")

class configdict(dict):
    special = {
            'meta': 'meta',
            'plates': 'plate',
            'rows': 'row',
            'irows': 'irow',
            'cols': 'col',
            'icols': 'icol',
            'blocks': 'block',
            'wells': 'well',
    }

    def __init__(self, config):
        self.update(config)

    def __getattr__(self, key):
        if key in self.special:
            return self.setdefault(self.special[key], {})

    def __setattr__(self, key, value):
        if key in self.special:
            self[self.special[key]] = value

    @property
    def user(self):
        return {k: v
                for k, v in self.items()
                if k not in self.special.values()
        }

