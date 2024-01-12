import os
import uuid
import click
import yaml
import pathlib
from pprint import pprint


config_file = os.path.join(os.path.dirname(__file__), "config.yaml")
with open(config_file) as fid:
    conf = yaml.safe_load(fid)

sites = list(conf)
pools = {p for site in sites for p in conf[site]["pool"]}

disclaimer = """

################################################################################
# DISCLAIMER: USE AT YOUR OWN RISK
# By executing this script generated by ptool, you acknowledge and accept that
# you are solely responsible for its consequences. Review and understand each
# command before execution. Make backups if you consider it necessary.
# The developers of ptool are not liable for any issues resulting from its use.
################################################################################

"""


def process_slurm_directives(d):
    result = []
    for key, val in d.items():
        if key.strip().startswith("--"):
            if val.strip():
                s = f"#SBATCH {key}={val}"
            else:
                s = f"#SBATCH {key}"
        else:
            if val.strip():
                s = f"#SBATCH {key} {val}"
            else:
                s = f"#SBATCH {key}"
        result.append(s)
    result = "\n".join(result)
    return result


@click.group()
def cli():
    pass


@cli.command()
@click.option(
    "--all",
    "showall",
    is_flag=True,
    default=False,
    help="Gets the whole config in the absence of arguments.",
)
@click.argument("site", required=False, default="")
@click.argument("pool", required=False, default="")
def config(showall, site, pool):
    "shows config information for a given site"
    if not site:
        if showall:
            print(yaml.dump(conf, default_flow_style=False))
        else:
            print(f"Possible values for SITE: {','.join(sites)}")
            print(f"Possible values for POOL: {','.join(pools)}")
        return
    if site not in sites:
        raise ValueError(f"Valid choices for Site: {sites}")
    if not pool:
        c = conf[site]
        print(yaml.dump(c, default_flow_style=False))
        return
    elif pool not in pools:
        raise ValueError(f"Valid choices for Pool: {pools}")
    c = {}
    c[site] = conf[site]
    c[site]["pool"] = {pool: conf[site]["pool"][pool]}
    print(yaml.dump(c, default_flow_style=False))
    return


@cli.command()
@click.option("-f", "--filename", help="name of the run script")
@click.argument("site", type=click.Choice(sites), required=True)
@click.argument("pool", type=click.Choice(pools), required=True)
def runscript(filename, site, pool):
    "makes run script for job submission"
    if site not in sites:
        raise ValueError(f"mismatch site '{site}'. Possible values: {sites}")
    if pool not in pools:
        raise ValueError(f"mismatch pool '{pool}'. Possible values: {pools}")
    c = conf[site]
    c_pool = c["pool"][pool]
    c_slurm = c["slurm"]
    c_ignore = ",".join(c_pool["ignore"]) if c_pool["ignore"] else ""
    c_extras = "\n".join(c["extras"])
    c_conf_str = yaml.dump(c_pool, default_flow_style=True, width=float("inf")).replace(
        "\n", ""
    )
    slurm_directives = process_slurm_directives(c_slurm)
    content = f"""#!/bin/bash

{slurm_directives}

{c_extras}

python checksums.py {c_pool['path']} --outfile {c_pool['outfile']} --ignore {c_ignore!r} 
"""
    if not filename:
        filename = f"{pool}_{site}.sh"
    f = pathlib.Path(filename)
    with open(f, "w") as fid:
        fid.write(content)
    print(f"created '{f}'.\nsubmit to slurm as 'sbatch {f}' on {site}")
    return


@cli.command()
@click.option(
    "-o", "--outfile", type=click.File("w"), default="-", help="file to write results"
)
@click.option(
    "--format",
    "fileformat",
    type=click.Choice(["csv", "yaml"]),
    default="csv",
    help="output format (default: csv)",
)
@click.option(
    "--fullpath",
    is_flag=True,
    required=False,
    help="displays full path instead of relative path",
)
@click.option("--ignore", help="ignores directory and files")
@click.argument("left", required=True, type=click.Path())
@click.argument("right", required=True, type=click.Path())
def compare(outfile, fileformat, fullpath, ignore, left, right):
    """Compare csv files containing checksum to infer the status of data
    in these data pools. The results include, synced files at both HPC sites. unsynced files.
    directory mapping of synced files. filename mis-matches.

    LEFT: csv file containing checksums of all files in the pool for a given project and HPC site.

    RIGHT: similar file as LEFT but from different HPC site for the same project.
    """
    from .analyse import read_csv, compare_compact, compare_directory_view

    ld, ld_dups = read_csv(left, ignore=ignore)
    lr, lr_dups = read_csv(right, ignore=ignore)
    if fileformat == "yaml":
        d = compare_directory_view(ld, lr, fullpath=fullpath)
        click.echo(f"Writing results as yaml to file {outfile.name}")
        yaml.dump(d, outfile, sort_keys=False)
        return
    columns = "rpath"
    if fullpath:
        columns = "fpath"
    res = compare_compact(ld, lr, columns=columns, relabel=True)
    if "stdout" in outfile.name:
        click.echo(res)
    else:
        click.echo(f"Writing results as csv to file {outfile.name}")
        res.to_csv(outfile)
        click.echo(res)


@cli.command()
@click.option("--ignore", help="ignores directory and files")
@click.option(
    "--compact",
    is_flag=True,
    show_default=True,
    default=False,
    help="shortern table by ignoring `unique` only association",
)
@click.argument("left", required=True, type=click.Path())
@click.argument("right", required=True, type=click.Path())
def summary(ignore, compact, left, right):
    """Prints a short summary by analysing csv files.

    LEFT: csv file containing checksums of all files in the pool for a given project and HPC site.

    RIGHT: similar file as LEFT but from different HPC site for the same project.
    """
    from .analyse import summary

    summary(left, right, ignore, compact)


def sanitise(host, path):
    "sanitise the hostpart of the path"
    if (not host) or ("awi.de" in host):
        return path
    return f"{host}:{path}"


@cli.command()
@click.option("--ignore", help="ignores directory and files")
@click.option(
    "--flags",
    "Flag",
    type=click.Choice(["unique", "modified", "both"]),
    default="both",
    help="association type to include",
)
@click.argument("left", required=True, type=click.Path())
@click.argument("right", required=True, type=click.Path())
def prepare_rsync(ignore, Flag, left, right):
    """Prepares rsync commands for the transfer."""
    if Flag == "both":
        Flag = {"unique", "modified_latest_left"}
    elif Flag == "modified":
        Flag = {
            "modified_latest_left",
        }
    else:
        Flag = {
            "unique",
        }
    from .analyse import read_csv, compare, compare_compact, directory_map, merge

    ld, ld_dups = read_csv(left, ignore=ignore)
    rd, rd_dups = read_csv(right, ignore=ignore)
    _, left_pool, left_site = left.split("_")
    left_site, _ = os.path.splitext(left_site)
    _, right_pool, right_site = right.split("_")
    right_site, _ = os.path.splitext(right_site)
    left_host = conf[left_site]["host"]
    right_host = conf[right_site]["host"]
    dm = dict(directory_map(merge(ld, rd)).values)
    c = compare(ld, rd)
    fmap = {}
    syncs = ["#!/bin/bash"]
    syncs.append(disclaimer)
    prefix_left = c.prefix_left.dropna().iloc[0]
    prefix_right = c.prefix_right.dropna().iloc[0]
    for name, grp in c.groupby("rparent_left"):
        use_relative = True
        if name in dm:
            use_relative = False
        filelist = []
        grp = grp.reset_index()
        flags = set(grp.flag.unique())
        if ("unique" in flags) and ("unique" in Flag):
            g = grp[grp.flag == "unique"]
            if use_relative:
                filelist.extend(list(g.rpath_left))
            else:
                filelist.extend(list(g.fname_left))
        if ("modified_latest_left" in flags) and ("modified_latest_left" in Flag):
            g = grp[grp.flag == "modified_latest_left"]
            if use_relative:
                filelist.extend(list(g.rpath_left))
            else:
                filelist.extend(list(g.fname_left))
        if filelist:
            fid = str(uuid.uuid4())[:8]
            fmap[fid] = filelist
            if use_relative:
                sync = f"rsync -av --files-from=flist/{fid} {sanitise(left_host, prefix_left)} {sanitise(right_host, prefix_right)}"
            else:
                rparent_left = name
                rparent_right = dm[name]
                sync = f"rsync -av --files-from=flist/{fid} {sanitise(left_host, prefix_left)}{rparent_left}/ {sanitise(right_host, prefix_right)}{rparent_right}/"
            syncs.append(f"\n# {name}")
            syncs.append(sync)
    syncs = "\n".join(syncs)
    os.makedirs("flist", exist_ok=True)
    with open("sync_cmd.sh", "w") as fid:
        fid.writelines(syncs)
    for name, fnames in fmap.items():
        with open(f"flist/{name}", "w") as fid:
            fnames = "\n".join(fnames)
            fid.writelines(fnames)
    print("Created sync_cmd.sh")


if __name__ == "__main__":
    cli()
