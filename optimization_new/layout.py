#! /usr/bin/python3

"""
This script uses the lucid tofino compiler to 
generate a dataflow graph, then lays that 
dataflow graph out on a model of the tofino 
pipeline to estimate how many stages it 
will require. The layout algorithm used 
in this script is the same as the one in the 
lucid backend (as of 10/4/22), except this script has some 
optimizations in it to reduce layout time. -jsonch

Usage: 
  with symb file: ./layout.py <input.dpt> --symb <input.symb>
  without symb file: ./layout.py <input.dpt>
"""

import json, re, sys, os, subprocess, argparse, random
import networkx as nx
from collections import namedtuple

script_dir = os.path.realpath(os.path.dirname(__file__))

parser = argparse.ArgumentParser()

def main():
  parser.add_argument('--symb', type=str, required=False)
  parser.add_argument('infn', type=str)
  parser.add_argument('--dfg', help="use dataflow analysis instead of layout", action="store_true")
  args = parser.parse_args()
  tmpjson = args.infn + ".layout.json"
  #mk_cmd = f"cd {script_dir}/..; make all"
  #print ("building lucid...")
  #subprocess.call(mk_cmd, shell=True)
  print (f"compiling {args.infn} to dataflow graph ({tmpjson})")
  if (args.symb):
    build_cmd = f"{script_dir}/dfgCompiler --symb {args.symb} {args.infn} -o {tmpjson}"
  else:
    build_cmd = f"{script_dir}/dfgCompiler {args.infn} -o {tmpjson}"
  subprocess.call(build_cmd, shell=True)
  print (f"laying out {tmpjson}")
  dg, groups = build_dependency_graph(tmpjson)
  if args.dfg:
    layout_dependencies_only(dg, groups)
    return
  pipe, resources = layout(dg, groups)
  sys.stdout.write("LAYOUTSTAGES")
  sys.stdout.write(json.dumps(resources))
  sys.stdout.write("LAYOUTSTAGES")
  return

Array = namedtuple("Array", "id ty size")
Variable = namedtuple("Variable", "id ty size")
Statement = namedtuple("Statement", "id statement user_table resources")

def statement_to_string(s):
  return s.statement.replace("\n", " ")

def arr_from_json(js): 
  return Array(js["id"], js["ty"], js["size"])
def var_from_json(js):
  return Variable(js["id"], js["ty"], js["size"])

# make a dictionary of all the resource that this statement uses
def stmt_resources(stmt, vars, arrs):
  r_any_op = r'\n(.*?);'
  ops = re.findall(r_any_op, stmt)
  array_ops, hash_ops, alu_ops = [], [], []
  for op in ops:
    if ("Array" in op):
      array_ops.append(op)
    elif ("hash" in op):
      hash_ops.append(op)
    else:
      alu_ops.append(op)

  array_ids = list(set(re.findall(r'.*Array..\.update.*?\((.*?),', stmt)))
  match_str = re.findall(r'match (.*?) with', stmt)[0]
  match_str = match_str.replace("(", "").replace(")", "").strip()
  match_vars = []
  if (match_str != ""):
    match_vars = [mv.strip() for mv in match_str.split(",")]
  match_vars = [vars[id] for id in match_vars]
  return {
    "arrays":[arrs[aid] for aid in array_ids],
    "keys":match_vars,
    "array_ops":array_ops, 
    "hash_ops":hash_ops, 
    "alu_ops":alu_ops
    }

def stmt_from_json(js, vars, arrs):
  stmt = js["statement"]
  user_table = False
  if (js["user_table"] == "true"):
    user_table = True
  return Statement(js["id"], stmt, user_table, stmt_resources(stmt, vars, arrs))


# we are laying out groups / bundles of statement. Each bundle contains 
# all the statements that access a particular register array.

def groupid_of_stmt(s):
  if (len(s.resources["arrays"]) == 1):
    return s.resources["arrays"][0].id
  elif (len(s.resources["arrays"]) == 0):
    return "stmt~%s"%(s.id)
  else:
    print ("error -- statement accesses more than 1 array. Impossible.")
    exit(1)

class StatementGroup (object):
  """ A group of statements that must go into the same stage
      (either because that all use the same array, or because 
      the layout algorithm has placed them in the same table) """  
  def __init__(self, gid):
    self.gid = gid
    self.statements = []
    self.user_table = False
    # resources required by this statement group
    self.resources = {
      "keybits":0,
      "sram_blocks":0,
      "arrays":0,
      "hash_ops":0,
      "array_ops":0
    }

  def calc_keybits(self):
    keys = []
    for stmt in self.statements:
      keys += stmt.resources["keys"]
    keys = list(set(keys))
    self.resources["keybits"] = sum([mv.size for mv in keys])

  def calc_sram(self):
    sram_block_size = 1024 * 128 # 128 kb
    arrays = []
    for stmt in self.statements:
      arrays += stmt.resources["arrays"]
    arrays = list(set(arrays))
    self.resources["sram_blocks"] = sum([(array.size // sram_block_size) + 2 for array in arrays])

  def count_resource(self, resource):
    units = []
    for stmt in self.statements:
      units += stmt.resources[resource]
    self.resources[resource] = len(list(set(units)))

  def calc_resources(self):
    """ calculate this statement groups resource footprint """
    self.calc_keybits()
    self.calc_sram()
    self.count_resource("arrays")
    self.count_resource("hash_ops")
    self.count_resource("array_ops")

  def add_stmt(self, stmt):
    self.statements = self.statements + [stmt]
    self.user_table = self.user_table or stmt.user_table
    # update resources
    self.calc_resources()

  def node_key(self):
    return (self.gid)#, {"obj":self})

  def __str__(self):
    return ("    ")+"\n    ".join([f"{i}: {statement_to_string(s)}" for i, s in enumerate(self.statements)])

  def resource_summary(self):
    rstr = "statements : %i"%len(self.statements)
    for (r, v) in self.resources.items():
      rstr+="\n%s : %i"%(r, v)
    return rstr


class Dependency(object):
  def __init__(self, srcid, dstid):
    self.srcid = srcid
    self.dstid = dstid
    self.dep_tys = []
  def add_dep_ty(self, dep_ty):
    self.dep_tys = self.dep_tys + [dep_ty]
  def edge_key(self):
    return (self.srcid, self.dstid)#, {"obj":self})

def build_dependency_graph(json_fn):
  prog_json = json.load(open(json_fn, "r"))
  arrs = {arr["id"]:arr_from_json(arr) for arr in prog_json["arrays"]}
  vars = {v["id"]:var_from_json(v) for v in prog_json["vars"]}
  stmts = {st["id"]:stmt_from_json(st, vars, arrs) for st in prog_json["statements"]}
  # generate statement groups
  groups = {}  # array or stmt id -> group
  for s in stmts.values():
    group_id = groupid_of_stmt(s)
    group = groups.get(group_id, StatementGroup(group_id))
    group.add_stmt(s)
    groups[group_id] = group
  # find dependencies between statement groups 
  deps = {} # (src, dst) -> dep obj
  for dep in prog_json["dependencies"]:
    src_group_id = groupid_of_stmt(stmts[dep["srcid"]])
    dst_group_id = groupid_of_stmt(stmts[dep["dstid"]])
    key = (src_group_id, dst_group_id)
    d = deps.get(key, Dependency(src_group_id, dst_group_id))
    d.add_dep_ty(dep["dep_ty"])
    deps[key] = d

  dg = nx.DiGraph()
  nodes = [sg.node_key() for sg in groups.values()][::-1]
  edges = [dep.edge_key() for dep in deps.values()][::-1]
  #random.shuffle(nodes)
  #random.shuffle(edges)
  dg.add_nodes_from(nodes)
  dg.add_edges_from(edges)
  return dg, groups

# find the minimum stage for each group based
# on previous dependencies. 
def min_stage_from_dependencies(group, dg, groups):
  min_stage = 0
  #if group.gid == "arr_short_1~3188":
  #  print("GROUP STATEMENT")
  for pred_gid in dg.predecessors(group.gid):
    pred_group = groups[pred_gid]
    #if group.gid == "arr_short_1~3188":
    #  print("PREDID", pred_gid, "STAGE", pred_group.stage)
    if (pred_group.stage == None):
      min_stage = None
    elif (min_stage == None):
      min_stage = None
    else:
      min_stage = max(min_stage, (pred_group.stage+1))
  return min_stage




# combine 2 statement groups
def merge_statement_groups(sg1, sg2):
  new_g = StatementGroup(sg1.gid)
  new_g.statements = sg1.statements + sg2.statements
  for s in new_g.statements:
    new_g.user_table = new_g.user_table or s.user_table
  new_g.calc_resources()
  return new_g

# check if a statement group obeys a list of constraints
def check_statement_group(sg, constraints):
  for resource, limit in constraints.items():
    if (sg.resources[resource] > limit):
      return False
  return True


# A simple resource model of the tofino pipeline: 
# a pipeline of stages, each stage has tables. 
# tables implement statements 
class Table(object):
  """ A physical tofino table """
  def __init__(self, table_id):
    # resource constraints
    self.constraints = {
      "keybits" : 512,
      "arrays" : 1,
      "sram_blocks": 35,
      "hash_ops" : 1,
      "array_ops": 4    # this is fine as long as a table has a single stmt group, and stmt group has at most 1 array
    }
    # contents
    self.table_id = table_id
    self.active = False
    self.statement_group = StatementGroup("table%i"%self.table_id) 

  def add_group(self, sg):
    """ place the statement group sg in this table. If it violates 
        a resource constraint, return false """
    # cannot merge two user tables
    if (self.statement_group.user_table and sg.user_table):
      return False
    new_g = merge_statement_groups(self.statement_group, sg)
    if (check_statement_group(new_g, self.constraints)):
      self.statement_group = new_g
      self.active = True
      return True
    else:
      return False

  def resource_summary(self):
    rstr = "-- table %s resources --\n"%(self.table_id)
    rstr +=self.statement_group.resource_summary()
    return rstr
  def __str__(self):
    s = f"  table table{self.table_id}"+"{\n"+f"{self.statement_group}"+"\n  }"
    return s


class Stage(object):
  """ A physical tofino stage """
  def __init__(self, stage_id, n_tables):
    self.stage_id = stage_id
    self.constraints = {
      "arrays" : 4,
      "sram_blocks": 48,
      "hash_ops" : 6
      #"array_ops": 4   # each array in stg can have up to 4 ops, not 4 total in stg
    }
    self.tables = [Table(i) for i in range(n_tables)]      
  
  # can the new statement group fit into this stage?
  def check_resources(self, new_statement_group):
    for resource, limit in self.constraints.items():
      total = new_statement_group.resources[resource] + sum([t.statement_group.resources[resource] for t in self.tables])
      if (total > limit):
        #if new_statement_group.gid == "arr_short_1~3188":
        #  print(new_statement_group)
        #  print("RES", resource)
        #  print("STMT GROUP RES", new_statement_group.resources[resource])
        #  print("TOTAL RES", total, "LIMIT", limit)
        return False
    return True

  def add_group(self, sg):
    if (not self.check_resources(sg)):
      return None
    loc = None
    for t in self.tables:
      success = t.add_group(sg)
      if success:
        loc = (self.stage_id, t.table_id)
        #print ("placed in (stage, table): (%i, %i)"%loc)
        break
    return loc
  def __str__(self):
    return f"stage {self.stage_id} "+"{\n"+"\n".join([str(t) for t in self.tables if t.active])+"\n}"

class Pipeline(object):
  """ The tofino match-action pipeline """
  def __init__(self, n_stages, n_tables):
    # contents
    self.stages = [Stage(i, n_tables) for i in range(n_stages)]
  def add_group(self, sg, min_stage):
    if not self.stages[min_stage::]:    # we've run out of stages
      print("ran out of stages")
      # arbitarily large val for stages so that we know we're > available
      resources = {"stages": 100}
      resfile = os.getcwd()+"/resources.json"
      with open(resfile,'w') as f:
        json.dump(resources, f, indent=4)
      exit(0)   # exit w/ code 0 bc this isn't really an error, just using too many resources
    for s in self.stages[min_stage::]:
      loc = s.add_group(sg)
      if loc is not None:
        return loc
    # TODO: return some error or something here? we hit this case ONLY when we run out of resources (??)
    print("RAN OUT OF RESOURCES")
    # arbitarily large val for stages so that we know this doesn't compile
    resources = {"stages": 100}
    resfile = os.getcwd()+"/resources.json"
    with open(resfile,'w') as f:
      json.dump(resources, f, indent=4)
    exit(0)
    return None
  def __str__(self):
    st = ""
    for s in self.stages:
      n_active = len([t for t in s.tables if t.active])
      if (n_active):
        st = st + str(s) + "\n"
    return st


def layout_dependencies_only(dg, groups):
  """ layout based only on dependencies -- this is a lower bound of stages """
  dep_stages = []
  for gid in nx.topological_sort(dg):
    dependency_stage = min_stage_from_dependencies(groups[gid], dg, groups)
    #print("DEP ONLY GID:", gid, "DEP STAGE:", dependency_stage)
    groups[gid].stage=dependency_stage
    dep_stages.append(dependency_stage)
  print (f"number of stages (dependencies only): {max(dep_stages)+1}")
  resources = {"stages": max(dep_stages)+1, "hash": 0, "regaccess": 0, "sram": 0}
  resfile = os.getcwd()+"/resources_dfg.json"
  with open(resfile,'w') as f:
    json.dump(resources, f, indent=4)
  return

def layout(dg, groups):
  """ layout based on dependencies and pipeline resources -- 
      this is approximately equal to lucid compiler, though there may be variations due 
      to the order in which statements are placed. """
  # setting stages = 30 --> we don't want to error out if we use > 12 stgs, just return estimate instead
  pipe = Pipeline(30, 16)
  print ("**** starting layout ****")  
  # layout can be done in a single topologically 
  # ordered pass through the statement groups
  for gid in nx.topological_sort(dg):
    #print ("placing: %s"%gid)
    dependency_stage = min_stage_from_dependencies(groups[gid], dg, groups)
    #print("FULL GID:", gid, "DEP STAGE:", dependency_stage)
    stage, table = pipe.add_group(groups[gid], dependency_stage)
    groups[gid].stage=stage

  #print ("**** layout finished ****")
  #print (str(pipe))
  #print ("**** resources ****")
  n_stages_used = 0
  hash_ops_used = 0
  array_ops_used = 0
  sram_blocks_used = 0
  for s in pipe.stages:
    n_active = len([t for t in s.tables if t.active])
    if (n_active):
      n_stages_used += 1
      #print ("---- stage %i [%i tables] ----"%(s.stage_id, n_active))
      for t in s.tables:
        if (t.active):
          #print (t.resource_summary())
          hash_ops_used += t.statement_group.resources["hash_ops"] 
          array_ops_used += t.statement_group.resources["array_ops"] 
          sram_blocks_used += t.statement_group.resources["sram_blocks"]

  #layout_dependencies_only(dg, groups)
  print (f"number of stages (full layout): {n_stages_used}")
  resources = {"stages": n_stages_used, "hash": hash_ops_used, "regaccess": array_ops_used, "sram": sram_blocks_used}
  resfile = os.getcwd()+"/resources.json"
  with open(resfile,'w') as f:
    json.dump(resources, f, indent=4)
  return pipe,resources

if __name__ == '__main__':
  main()
