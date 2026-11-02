
def verify_product_files(app, expected_product, read_public, *, expected_uid=10001):
    import os,stat
    from pathlib import Path,PurePosixPath
    from hashlib import sha256
    app=Path(app);expected={row['path']:row for row in expected_product}
    assert expected and len(expected)==len(expected_product),'candidate_public_inventory'
    trees=('core/market_intelligence','contracts/market_data','deploy/market-data/migrations')
    expected_dirs={str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent)!='.'}
    def directory(path):
        info=path.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid==info.st_gid==expected_uid and stat.S_IMODE(info.st_mode)==0o755,'candidate_public_directory_metadata'
    directory(app)
    for name in expected_dirs:directory(app/name)
    actual={'core/__init__.py','config/market_data_sources.v1.json'}
    actual_dirs={name for name in expected_dirs if not any(name==tree or name.startswith(tree+'/') for tree in trees)}
    count=0
    def walk(path):
        nonlocal count
        directory(path);actual_dirs.add(path.relative_to(app).as_posix())
        # scandir propagates permission errors; pathlib.rglob can suppress them.
        with os.scandir(path) as entries:
            for entry in entries:
                count+=1;assert count<=10000,'candidate_public_inventory_bound'
                assert not entry.is_symlink(),'candidate_public_symlink'
                if entry.is_dir(follow_symlinks=False):walk(Path(entry.path))
                else:
                    assert entry.is_file(follow_symlinks=False),'candidate_public_file_type'
                    actual.add(Path(entry.path).relative_to(app).as_posix())
    for tree in trees:walk(app/tree)
    assert actual==set(expected) and actual_dirs==expected_dirs,'candidate_public_inventory'
    for name,row in expected.items():
        path=app/name;raw=read_public(path,4194304);info=path.lstat()
        assert len(raw)==row['bytes'] and sha256(raw).hexdigest()==row['sha256'] and info.st_uid==info.st_gid==expected_uid,'candidate_public_file_identity'
        assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode)==(0o755 if row['mode']=='100755' else 0o644),'candidate_public_file_metadata'
    return True
