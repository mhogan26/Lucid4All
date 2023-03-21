import time, importlib, argparse, os, sys, json
from preprocess import *
from optalgos import *
from interp_sim import update_sym_sizes, write_symb

def init_opt(optfile, notraffic, cwd):
    sys.path.append(cwd)
    # NOTE: assuming optfile is in current working directory
    # import json file
    opt_info = json.load(open(optfile))

    # import opt class that has funcs we need to get traffic, cost
    # NOTE: module has to be in current working directory
    optmod = importlib.import_module(opt_info["optmodule"])
    o = optmod.Opt(opt_info["trafficpcap"])

    # gen traffic
    if not notraffic:
        o.gen_traffic()

    # it's easier for gen next values if we exclude logs and don't separate symbolics/sizes, so let's do that here
    symbolics_opt = {}

    # is there a better way to merge? quick solution for now
    for var in opt_info["symbolicvals"]["sizes"]:
        if var not in opt_info["symbolicvals"]["logs"]:
            symbolics_opt[var] = opt_info["symbolicvals"]["sizes"][var]
    for var in opt_info["symbolicvals"]["symbolics"]:
        if var not in opt_info["symbolicvals"]["logs"]:
            symbolics_opt[var] = opt_info["symbolicvals"]["symbolics"][var]

    return opt_info,symbolics_opt, o

def init_opt_trace(optfile, cwd):
    sys.path.append(cwd)
    # NOTE: assuming optfile is in current working directory
    # import json file
    opt_info = json.load(open(optfile))

    # import opt class that has funcs we need to get traffic, cost
    # NOTE: module has to be in current working directory
    optmod = importlib.import_module(opt_info["optmodule"])
    o = optmod.Opt()

    symbolics_opt = {}
    # is there a better way to merge? quick solution for now
    for var in opt_info["symbolicvals"]["sizes"]:
        symbolics_opt[var] = opt_info["symbolicvals"]["sizes"][var]
    for var in opt_info["symbolicvals"]["symbolics"]:
        symbolics_opt[var] = opt_info["symbolicvals"]["symbolics"][var]

    trace_params = opt_info["traceparams"]
    trace_bounds = opt_info["tracebounds"]

    return opt_info,symbolics_opt, o, trace_params, trace_bounds

# usage: python3 optimize.py <json opt info file>
def main():
    parser = argparse.ArgumentParser(description="optimization of lucid symbolics in python, default uses layout script instead of compiler")
    parser.add_argument("optfile", metavar="optfile", help="name of json file with optimization info")
    parser.add_argument("--timetest", help="time test, output results at benchmark times", action="store_true")
    parser.add_argument("--notrafficgen", help="don't call gen_traffic, this is just for testing", action="store_true")
    parser.add_argument("--fullcompile", help="use lucid-p4 compiler instead of layout script", action="store_true")
    parser.add_argument("--pair", help="hacky solution to identify when we have pair arrays", action="store_true")
    parser.add_argument("--preprocessingonly", help="only do preprocessing, store sols in preprocessed.pkl", action="store_true")
    parser.add_argument("--shortcut", help="don't do preprocessing, load already preprocessed sols from preprocessed.pkl", action="store_true")
    parser.add_argument("--dfg", help="use dataflow analysis instead of layout in preprocessing", action="store_true")
    args = parser.parse_args()

    '''
    if len(sys.argv) < 2:
        print("usage: python3 optimize.py <json opt info file>")
        quit()
    '''
    #opt_info = json.load(open(sys.argv[1]))
    #print(opt_info)

    # get current working directory
    cwd = os.getcwd()

    # TODO: this duplicates what's already in init_opt, either remove it from the function or remove it here
    opt_info = json.load(open(args.optfile))

    '''
    OPTIMIZE TRACE: keep symbolic values/struct configuration the same; change attributes of the input trace each iteration
    '''
    if opt_info["optparams"]["optalgo"] == "trace":
        opt_info, symbolics_opt, o, trace_params, trace_bounds = init_opt_trace(args.optfile, cwd)
        # write symbolic file w/ vals given in json
        update_sym_sizes(symbolics_opt, opt_info["symbolicvals"]["sizes"], opt_info["symbolicvals"]["symbolics"])
        write_symb(opt_info["symbolicvals"]["sizes"], opt_info["symbolicvals"]["symbolics"], [], opt_info["symfile"], opt_info)

        # TODO: put this in a loop/function outside of optimize.py (e.g., optalgos.py)
        if opt_info["optparams"]["strategy"] == "random":
            # generate trace
            o.gen_traffic(trace_params)
            # get cost
            cost = gen_cost(symbolics_opt,symbolics_opt,opt_info, o,False, "trace")
            print(cost)
        else:
            exit("input strategy is not implemented for trace version of parasol")

    else:
        '''
        OPTIMIZE SYMBOLICS: original parasol, each iteration tests different data structure configuration
        '''
        # initialize everything we need to run opt algo
        opt_info,symbolics_opt, o = init_opt(args.optfile, args.notrafficgen, cwd)

        bounds_tree = None
        solutions = None

        # check if we're doing preprocessing
        if "optalgo" in opt_info["optparams"] and opt_info["optparams"]["optalgo"] == "preprocess":
            sols = preprocess(symbolics_opt, opt_info, o, args.timetest, args.fullcompile, args.pair, args.shortcut, args.dfg)
            bounds_tree = sols["tree"]
            solutions = sols["all_sols"]

            # TODO: save preprocessed sols every time???
            if args.preprocessingonly:  # only preprocessing, no optimization
                if args.dfg: # we used dataflow graph heuristic instead of layout
                    with open('preprocessed_dfg.pkl','wb') as f:
                        pickle.dump(sols, f)
                    return
                with open('preprocessed.pkl','wb') as f:
                    pickle.dump(sols, f)
                return


        # optimize!
        start_time = time.time()
        # TODO: allow user to pass in func
        if "optalgofile" in opt_info["optparams"]:   # only include field in json if using own algo
            # import module, require function to have standard name and arguments
            user = True

        elif opt_info["optparams"]["strategy"] == "simannealing":
            best_sols, best_cost = simulated_annealing(symbolics_opt, opt_info, o, args.timetest, solutions, bounds_tree)

        elif opt_info["optparams"]["strategy"] == "exhaustive":
            best_sols, best_cost = exhaustive(symbolics_opt, opt_info, o, args.timetest, solutions, bounds_tree)

        elif opt_info["optparams"]["strategy"] == "bayesian":
            best_sols, best_cost = bayesian(symbolics_opt, opt_info, o, args.timetest, solutions, bounds_tree)

        elif opt_info["optparams"]["strategy"] == "neldermead":
            best_sols, best_cost = nelder_mead(symbolics_opt, opt_info, o, args.timetest, solutions=solutions, tree=bounds_tree)


        end_time = time.time()
        # write symb with final sol
        update_sym_sizes(best_sol, opt_info["symbolicvals"]["sizes"], opt_info["symbolicvals"]["symbolics"])
        write_symb(opt_info["symbolicvals"]["sizes"], opt_info["symbolicvals"]["symbolics"], opt_info["symbolicvals"]["logs"], opt_info["symfile"], opt_info)



        '''
        # try compiling to tofino?
        # we could test the top x solutions to see if they compile --> if they do, we're done!
        # else, we can repeat optimization, excluding solutions we now know don't compile
        # (we have to have a harness p4 file for this step, but not for interpreter)
        # NOTE: use vagrant vm to compile
        for sol in top_sols:
            write_symb(sol[0],sol[1])
            # compile lucid to p4
            cmd_lp4 = ["../../dptc cms_sym.dpt ip_harness.p4 linker_config.json cms_sym_build --symb cms_sym.symb"]
            ret_lp4 = subprocess.run(cmd_lp4, shell=True)
            # we shouldn't have an issue compiling to p4, but check anyways
            if ret_lp4.returncode != 0:
                print("error compiling lucid code to p4")
                break
            # compile p4 to tofino
            cmd_tof = ["cd cms_sym_build; make build"]
            ret_tof = subprocess.run(cmd_tof, shell=True)
            # return value of make build will always be 0, even if it fails to compile
            # how can we check if it compiles????

            # if compiles, break bc we've found a soluion
        '''


        print("BEST:")
        print(best_sols[0])
        print("BEST COST:")
        print(best_cost)
        print("TIME(s):")
        print(end_time-start_time)



if __name__ == "__main__":
    main()



'''
json fields:
    symbolicvals: (anything info related to symbolics)
        sizes: symbolic sizes and starting vals
        symbolics: symbolic vals (ints, bools) and starting vals
        logs: which (if any) symbolics are log2(another symbolic)
        bounds: [lower,upper] bounds for symbolics, inclusive (don't need to include logs, bc they're calculated from other syms)
    structchoice: (tells us if we're choosing between structs)
        var: which of the symbolic vars corresponds to struct choice (boolean)
        True: if var==True, list any symbolic vars the corresponding struct doesn't use
        False: if var==False, ^
    optparams: (any info related to optimization algo)
        optalgo: if using one of our provided functions, tell us the name (simannealing, bayesian, neldermead)
        optalgofile: if using your own, tell us where to find it (python file)
        stop_iter: num iterations to stop at
        stop_time: time to stop at (in seconds)
        temp: initial temp for simannealing (init temps are almost arbitrary??)
        stepsize: stddev for simannealing (per symbolic? or single?
        maxcost: cost to return if solution uses too many stages
    symfile: file to write symbolics to
    lucidfile: dpt file
    outputfiles: list of files that output is stored in (written to by externs)
    optmodule: name of module that has class w/ necessary funcs
    trafficpcap: name of pcap file to use

sys reqs:
    python3
    lucid
    numpy

'''
