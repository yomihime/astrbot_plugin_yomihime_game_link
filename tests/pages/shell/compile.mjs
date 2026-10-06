import {stripTypeScriptTypes} from 'node:module';
import {readFile} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {resolve,dirname,extname} from 'node:path';
const cache = new Map();
export async function sourceModule(path) {
  const full=resolve(path); if(cache.has(full)) return import(cache.get(full));
  const source=await readFile(full,'utf8');
  let output=stripTypeScriptTypes(source,{mode:'strip',sourceUrl:full});
  const matches=[...output.matchAll(/from\s+(['"])(\.[^'"]+)\1/g)];
  for(const match of matches) {
    const target=resolve(dirname(full),match[2]); const url=extname(target)==='.js'?pathToFileURL(target).href:await sourceURL(extname(target)?target:target+'.ts');
    output=output.replace(match[0],`from ${JSON.stringify(url)}`);
  }
  const url='data:text/javascript;base64,'+Buffer.from(output).toString('base64'); cache.set(full,url); return import(url);
}
async function sourceURL(path) {await sourceModule(path);return cache.get(resolve(path));}
export const root=fileURLToPath(new URL('../../../',import.meta.url));
