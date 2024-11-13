all: $(patsubst %.yaml,%, $(filter-out candidates.yaml, $(wildcard *.yaml)))

%: campaign/%
	svp --debug batch publish $<

campaign/%: %.yaml
	svp --debug batch generate --recipe `pwd`/$< --candidates `pwd`/candidates.yaml $@
