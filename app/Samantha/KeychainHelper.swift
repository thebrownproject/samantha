import Foundation
import Security
import os

private let log = Logger(subsystem: "com.samantha.app", category: "KeychainHelper")

enum KeychainHelper {
    private static let service = "com.thebrownproject.samantha"
    private static let apiKeyAccount = "OpenAIAPIKey"

    private static var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: apiKeyAccount
        ]
    }

    @discardableResult
    static func saveAPIKey(_ key: String) -> Bool {
        guard let data = key.data(using: .utf8) else { return false }

        // Delete-before-add to avoid errSecDuplicateItem
        let deleteStatus = SecItemDelete(baseQuery as CFDictionary)
        if deleteStatus != errSecSuccess && deleteStatus != errSecItemNotFound {
            log.error("Failed to clear existing API key (status: \(deleteStatus))")
        }

        var addQuery = baseQuery
        addQuery[kSecValueData as String] = data
        addQuery[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlocked

        let status = SecItemAdd(addQuery as CFDictionary, nil)
        if status == errSecSuccess {
            log.info("API key saved to Keychain")
            return true
        }
        log.error("Failed to save API key (status: \(status))")
        return false
    }

    static func loadAPIKey() -> String? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else {
            if status != errSecItemNotFound {
                log.error("Failed to load API key (status: \(status))")
            }
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func deleteAPIKey() -> Bool {
        let status = SecItemDelete(baseQuery as CFDictionary)
        if status == errSecSuccess || status == errSecItemNotFound {
            log.info("API key deleted from Keychain")
            return true
        }
        log.error("Failed to delete API key (status: \(status))")
        return false
    }
}
