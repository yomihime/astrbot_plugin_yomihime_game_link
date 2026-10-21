import {lstat, readdir, mkdir, readFile, writeFile, rename, rm} from 'node:fs/promises';
import {resolve, dirname, parse, relative, sep} from 'node:path';
import {randomUUID} from 'node:crypto';

async function checkDestination(path) {
  const components=[];
  for(let current=resolve(path);;current=dirname(current)){
    components.unshift(current);if(current===parse(current).root)break;
  }
  for(const component of components){
    let info;
    try{info=await lstat(component);}catch(error){if(error.code==='ENOENT')continue;throw error;}
    if(info.isSymbolicLink())throw Error('Build output cannot traverse a link or reparse point: '+component);
    if(info.isFile()&&info.nlink>1)throw Error('Build output cannot overwrite a hard link: '+component);
    if(component!==path&&!info.isDirectory())throw Error('Build output parent must be a directory: '+component);
    if(component===path&&!info.isFile())throw Error('Build output must be a regular file: '+component);
  }
}

export async function publishBuild(stageRoot, outputRoot) {
  stageRoot=resolve(stageRoot);outputRoot=resolve(outputRoot);
  const files=[];
  async function collect(directory){
    for(const entry of await readdir(directory,{withFileTypes:true})){
      const source=resolve(directory,entry.name);
      if(entry.isSymbolicLink())throw Error('Staged output cannot be a link');
      if(entry.isDirectory())await collect(source);
      else if(entry.isFile()){
        const name=relative(stageRoot,source),destination=resolve(outputRoot,name);
        if(name.startsWith('..'+sep)||name==='..'||destination===source)throw Error('Build output overlaps an input');
        files.push({source,destination});
      }else throw Error('Staged output must be a regular file');
    }
  }
  await collect(stageRoot);
  // Check the entire batch before changing even one previous projection.
  for(const {destination} of files)await checkDestination(destination);
  const prepared=[];
  try{
    for(const {source,destination} of files){
      await mkdir(dirname(destination),{recursive:true});
      const temporary=resolve(dirname(destination),'.ygl-build-'+randomUUID()+'.tmp');
      await writeFile(temporary,await readFile(source),{flag:'wx'});prepared.push({temporary,destination});
    }
    for(const {destination} of files)await checkDestination(destination);
    for(const {temporary,destination} of prepared)await rename(temporary,destination);
  }finally{
    for(const {temporary} of prepared)await rm(temporary,{force:true});
  }
}
