.PHONY: opttest interp compile layout assemble test optimize_timetest dfg preprocess_dfg
LUCID_DIR = ../../lucid
OPT_DIR = ../optimization_new

# run optimization process (using layout, no traffic gen)
opttest:
	python3 $(OPT_DIR)/optimize.py cacheopt.json --notrafficgen --preprocessingonly

preprocess_dfg:
	python3 $(OPT_DIR)/optimize.py cacheopt.json --notrafficgen --preprocessingonly --dfg

preprocess_efficient:
	python3 $(OPT_DIR)/optimize.py cacheopt.json --notrafficgen --preprocessingonly 

# run p4all optimizer, time test
optimize_timetest:
	python3 $(OPT_DIR)/optimize.py cacheopt.json --timetest --shortcut

# run interpreter
interp: 
	$(LUCID_DIR)/dpt --suppress-final-state caching.dpt

# compile Lucid to P4
compile: 
	$(LUCID_DIR)/dptc caching.dpt --symb caching.symb --silent

#layout
layout:
	$(LUCID_DIR)/bin/layout.py caching.dpt --symb caching.symb

#dfg compile
dfg:
	$(LUCID_DIR)/bin/layout.py caching.dpt --symb caching.symb --dfg

# compile P4 to tofino
assemble:
	cd lucid_tofino_build; make lucid_tofino_build	 

# test P4 on the asic model
test:
	cd lucid_tofino_build; make test ../cmscache.json
