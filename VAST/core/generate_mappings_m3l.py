import csdl
from lsdo_modules.module.module import Module
from lsdo_modules.module_csdl.module_csdl import ModuleCSDL
from VAST.core.submodels.input_submodels.create_input_model import CreateACSatesModel
from VAST.core.submodels.input_submodels.create_input_module import CreateACSatesModule
from VAST.core.vlm_llt.vlm_solver import VLMSolverModel

from VAST.core.fluid_problem import FluidProblem
import m3l


class VASTNodelDisplacements(m3l.ExplicitOperation):
    def initialize(self, kwargs):
        self.parameters.declare(surface_names, types=list)
        self.parameters.declare(surface_shapes, types=list)
        self.parameters.declare(initial_mesh, default=list)

    def compute(self, nodal_displacements, nodal_displacements_mesh):
        surface_names = self.parameters['surface_names']  
        surface_shapes = self.parameters['surface_shapes']
        initial_mesh = self.parameters['initial_mesh']

        csdl_model = ModuleCSDL()

        for i in range(len(surface_names)):
            surface_name = surface_names[i]
            surface_shape = surface_shapes[i]
            out_shape = int((surface_shape[1]-1)*(surface_shape[2]-1))
            initial_mesh = initial_mesh[i]
            displacements_map = self.fmap(initial_mesh.reshape((-1,3)), oml=nodal_displacements_mesh.reshape((-1,3)))

            nodal_displacements = csdl_model.register_module_input(name=surface_name+'nodal_forces', shape=nodal_displacements.shape)
            displacements_map_csdl = csdl_model.create_input(f'nodal_to_{surface_name}_forces_map', val=displacements_map)
            flatenned_vlm_mesh_displacements = csdl.matmat(displacements_map_csdl, nodal_displacements)
            vlm_mesh_displacements = csdl.reshape(flatenned_vlm_mesh_forces, new_shape=(num_nodes,out_shape,3 ))
            csdl_model.register_module_output(f'{surface_name}_displacements', vlm_mesh_displacements)

        return csdl_model

    def evaluate(self, nodal_displacements, nodal_displacements_mesh):
        '''
        Maps nodal displacements_mesh from arbitrary locations to the mesh nodes.
        
        Parameters
        ----------
        nodal_displacements : a list of m3l.Variable
            The nodal_displacements to be mapped to the mesh nodes.
        nodal_displacements_mesh : a list of am.MappedArray
            The mesh that the nodal displacements_mesh are currently defined over.

        Returns
        -------
        mesh_forces : m3l.Variable
            The forces on the mesh.
        '''
        operation_csdl = self.compute(nodal_displacements=nodal_displacements, nodal_displacements_mesh=nodal_displacements_mesh)

        # beam_name = list(self.parameters['beams'].keys())[0]   # this is only taking the first mesh added to the solver.
        # mesh = list(self.parameters['mesh'].parameters['meshes'].values())[0]   # this is only taking the first mesh added to the solver.
        arguments = {}
        for i in range(len(surface_names)):
            arguments[surface_names[i]+'_displacements'] = nodal_displacements[i]

        displacement_map_operation = m3l.CSDLOperation(name='ebbeam_force_map', arguments=arguments, operation_csdl=operation_csdl)
        output_shape = tuple(mesh.shape[:-1]) + (nodal_forces.shape[-1],)
        beam_forces = m3l.Variable(name=f'{beam_name}_forces', shape=output_shape, operation=force_map_operation)
        return beam_forces


    def fmap(self, mesh, oml):
        # Fs = W*Fp

        x, y = mesh.copy(), oml.copy()
        n, m = len(mesh), len(oml)

        d = np.zeros((m,2))
        for i in range(m):
            dist = np.sum((x - y[i,:])**2, axis=1)
            d[i,:] = np.argsort(dist)[:2]

        # create the weighting matrix:
        weights = np.zeros((n, m))
        for i in range(m):
            ia, ib = int(d[i,0]), int(d[i,1])
            a, b = x[ia,:], x[ib,:]
            p = y[i,:]

            length = np.linalg.norm(b - a)
            norm = (b - a)/length
            t = np.dot(p - a, norm)
            # c is the closest point on the line segment (a,b) to point p:
            c =  a + t*norm

            ac, bc = np.linalg.norm(c - a), np.linalg.norm(c - b)
            l = max(length, bc)
            
            weights[ia, i] = (l - ac)/length
            weights[ib, i] = (l - bc)/length

        return weights

class EBBeamNodalDisplacements(m3l.ExplicitOperation):
    def initialize(self, kwargs):
        self.parameters.declare('mesh', default=None)
        self.parameters.declare('beams', default={})

    def compute(self, beam_displacements:m3l.Variable, nodal_displacements_mesh:am.MappedArray)->csdl.Model:
        beam_name = list(self.parameters['beams'].keys())[0]   # this is only taking the first mesh added to the solver.
        mesh = list(self.parameters['mesh'].parameters['meshes'].values())[0]   # this is only taking the first mesh added to the solver.

        csdl_model = ModuleCSDL()

        displacement_map = self.umap(mesh.value.reshape((-1,3)), oml=nodal_displacements_mesh.value.reshape((-1,3)))

        beam_displacements = csdl_model.register_module_input(name=f'{beam_name}_displacement', shape=beam_displacements.shape)
        displacement_map_csdl = csdl_model.create_input(f'{beam_name}_displacements_to_nodal_displacements', val=displacement_map)
        nodal_displacements = csdl.matmat(displacement_map_csdl, beam_displacements)
        csdl_model.register_module_output(f'{beam_name}_nodal_displacements', nodal_displacements)

        return csdl_model

    def evaluate(self, beam_displacements:m3l.Variable, nodal_displacements_mesh:am.MappedArray) -> m3l.Variable:
        '''
        Maps nodal forces and moments from arbitrary locations to the mesh nodes.
        
        Parameters
        ----------
        beam_displacements : m3l.Variable
            The displacements to be mapped from the beam mesh to the desired mesh.
        nodal_displacements_mesh : m3l.Variable
            The mesh to evaluate the displacements over.

        Returns
        -------
        nodal_displacements : m3l.Variable
            The displacements on the given nodal displacements mesh.
        '''
        operation_csdl = self.compute(beam_displacements, nodal_displacements_mesh)

        beam_name = list(self.parameters['beams'].keys())[0]   # this is only taking the first mesh added to the solver.

        arguments = {f'{beam_name}_displacements': beam_displacements}
        displacement_map_operation = m3l.CSDLOperation(name='ebbeam_displacement_map', arguments=arguments, operation_csdl=operation_csdl)
        nodal_displacements = m3l.Variable(name=f'{beam_name}_nodal_displacements', shape=nodal_displacements_mesh.shape, 
                                           operation=displacement_map_operation)
        return nodal_displacements


    def umap(self, mesh, oml):
        # Up = W*Us

        x, y = mesh.copy(), oml.copy()
        n, m = len(mesh), len(oml)

        d = np.zeros((m,2))
        for i in range(m):
            dist = np.sum((x - y[i,:])**2, axis=1)
            d[i,:] = np.argsort(dist)[:2]

        # create the weighting matrix:
        weights = np.zeros((m,n))
        for i in range(m):
            ia, ib = int(d[i,0]), int(d[i,1])
            a, b = x[ia,:], x[ib,:]
            p = y[i,:]

            length = np.linalg.norm(b - a)
            norm = (b - a)/length
            t = np.dot(p - a, norm)
            # c is the closest point on the line segment (a,b) to point p:
            c =  a + t*norm

            ac, bc = np.linalg.norm(c - a), np.linalg.norm(c - b)
            l = max(length, bc)
            
            weights[i, ia] = (l - ac)/length
            weights[i, ib] = (l - bc)/length

        return weights