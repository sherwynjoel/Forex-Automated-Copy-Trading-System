import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../lib/api'

const navItems = [
  { path: '/', label: 'Overview' },
  { path: '/accounts', label: 'Accounts' },
  { path: '/positions', label: 'Positions' },
  { path: '/logs', label: 'Logs' },
]

export default function Layout() {
  const location = useLocation()
  const navigate = useNavigate()

  const handleLogout = async () => {
    try {
      await api('/api/logout', { method: 'POST' })
      navigate('/login')
    } catch (err) {
      console.error('Logout failed:', err)
      navigate('/login')
    }
  }

  return (
    <div className="flex h-screen bg-gray-100">
      {/* Sidebar */}
      <div className="w-64 bg-gray-900 text-white shadow-lg">
        <div className="p-6">
          <h1 className="text-2xl font-bold">Forex Dashboard</h1>
        </div>

        <nav className="mt-6">
          {navItems.map((item) => (
            <Link
              key={item.path}
              to={item.path}
              className={`block px-6 py-3 transition-colors ${
                location.pathname === item.path
                  ? 'bg-blue-600 text-white'
                  : 'text-gray-300 hover:bg-gray-800'
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="absolute bottom-0 w-64 border-t border-gray-700 p-4">
          <button
            onClick={handleLogout}
            className="w-full text-left px-6 py-3 text-gray-300 hover:bg-gray-800 rounded-md transition-colors"
          >
            Logout
          </button>
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Header */}
        <div className="bg-white shadow">
          <div className="px-6 py-4">
            <h2 className="text-2xl font-semibold text-gray-900">
              {navItems.find((item) => item.path === location.pathname)?.label || 'Page'}
            </h2>
          </div>
        </div>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto">
          <div className="p-6">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
